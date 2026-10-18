"""Розбір документів у блоки тексту з розділом і сторінкою (D-14, D-17, D-18).

Формат визначає розширення; функції синхронні й важкі для CPU — їх викликають
через `asyncio.to_thread`. Файл приходить від звіра, тож:
- XML читаємо через defusedxml (без XML-бомб і зовнішніх сутностей);
- zip-члени — з лімітом розпакованого розміру;
- зовнішні програми (tesseract, antiword) — без shell і з таймаутом.
"""

import io
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from xml.etree.ElementTree import Element

import numpy as np
import pypdfium2 as pdfium
from defusedxml import DefusedXmlException, ElementTree
from striprtf.striprtf import rtf_to_text

logger = logging.getLogger(__name__)

SUPPORTED = (".pdf", ".docx", ".doc", ".rtf", ".odt", ".txt", ".md")
# Розпакований XML документа більший — це не порада, а zip-бомба
MAX_XML_BYTES = 30 * 1024 * 1024
# Сторінка, на якій менше символів, вважається сканом → OCR
MIN_PAGE_CHARS = 20
OCR_MAX_PAGES = 30
OCR_LANGS = "ukr+eng"
OCR_TIMEOUT = 120
# Рендеримо скан у його рідній роздільності: більше — повільніше без виграшу
# (на сканах архіву 300 DPI проти 150 — 40 с проти 5 с, точність та сама)
OCR_DPI_MIN, OCR_DPI_MAX, OCR_DPI_DEFAULT = 150, 300, 200
# Пікселі світліші — фон, плями, тіні скану; текст лишається
OCR_THRESHOLD = 120
TOOL_TIMEOUT = 60
HEADING_MAX = 90
TITLE_MAX = 150

# pdfium не потокобезпечний: два документи одночасно — через замок
_PDFIUM = threading.Lock()
_SENTENCE_END = tuple(".!?…:;,")
_BULLET = re.compile(r"^\s*(?:[-–—•*·]|\d+[.)])\s+")
# Стилі заголовків Word і ODF, зокрема локалізовані: «Заголовок 1», «Назва»
_HEADING_STYLE = re.compile(r"^(heading|title|заголовок|назва)", re.IGNORECASE)


class ParseError(Exception):
    """Документ не прочитано; повідомлення — для звіра."""


@dataclass(frozen=True)
class Block:
    text: str
    section: str | None = None
    page: int | None = None
    heading: bool = False


@dataclass(frozen=True)
class ParsedDocument:
    blocks: tuple[Block, ...]
    title: str | None
    ocr_pages: int = 0
    pages: int | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)


def parse(data: bytes, file_name: str) -> ParsedDocument:
    """Байти файлу → блоки; непідтримуваний чи порожній документ → ParseError."""
    extension = PurePath(file_name).suffix.lower()
    parser = _PARSERS.get(extension)
    if parser is None:
        raise ParseError(f"Такий формат не читаю. Підходять: {', '.join(SUPPORTED)}")
    document = parser(data)
    if not document.text.strip():
        raise ParseError("У документі не знайшлося тексту")
    return document


# --- Структура з рядків: TXT, MD, PDF, OCR, .doc, .rtf ---


def _is_heading(line: str) -> bool:
    line = line.strip()
    return (
        0 < len(line) <= HEADING_MAX
        and not line.endswith(_SENTENCE_END)
        and line[0].isupper()
        and not _BULLET.match(line)
    )


def _join(lines: list[str]) -> str:
    """Рядки абзацу → текст: перенос усередині речення — пробіл, пункти списку — з нового рядка."""
    out: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out and not _BULLET.match(line):
            if out[-1].endswith("-") and line[:1].islower():
                out[-1] = out[-1][:-1] + line
            else:
                out[-1] += " " + line
        else:
            out.append(line)
    return "\n".join(out)


def blocks_from_lines(
    lines: Iterable[str], *, page: int | None = None, section: str | None = None
) -> tuple[list[Block], str | None]:
    """Рядки з порожніми рядками між абзацами → блоки (TXT, MD, OCR, .doc, .rtf).

    Заголовок — `# …` у Markdown або окремий короткий рядок без розділового знака.
    Повертає блоки й розділ, у якому текст закінчився (для наступної сторінки PDF).
    """
    blocks: list[Block] = []
    group: list[str] = []

    def flush() -> None:
        nonlocal section
        if len(group) == 1 and _is_heading(group[0]):
            section = group[0].strip()
            blocks.append(Block(section, section, page, heading=True))
        elif text := _join(group):
            blocks.append(Block(text, section, page))
        group.clear()

    for raw in lines:
        line = raw.replace("\u00a0", " ").rstrip()
        if markdown := re.match(r"^#{1,6}\s+(.+)$", line):
            flush()
            section = markdown.group(1).strip()
            blocks.append(Block(section, section, page, heading=True))
        elif not line.strip():
            flush()
        else:
            group.append(line)
    flush()
    return blocks, section


@dataclass(frozen=True)
class _PdfLine:
    text: str
    size: float
    weight: int
    right: float


def _pdf_lines(page: pdfium.PdfPage) -> list[_PdfLine]:
    """Рядки текстового шару з розміром і вагою шрифту першої літери та правим краєм."""
    textpage = page.get_textpage()
    lines = []
    position = 0
    for text in textpage.get_text_range().split("\r\n"):
        if text.strip():
            last = position + len(text) - 1
            lines.append(
                _PdfLine(
                    text=text.strip(),
                    size=round(pdfium.raw.FPDFText_GetFontSize(textpage.raw, position), 1),
                    weight=pdfium.raw.FPDFText_GetFontWeight(textpage.raw, position),
                    right=textpage.get_charbox(last)[2],
                )
            )
        position += len(text) + 2
    return lines


def _body_font(pages: list[list[_PdfLine]]) -> tuple[float, int]:
    """Основний шрифт документа — той, яким набрано найбільше символів."""
    sizes: dict[tuple[float, int], int] = {}
    for line in (line for page in pages for line in page):
        key = (line.size, line.weight)
        sizes[key] = sizes.get(key, 0) + len(line.text)
    return max(sizes, key=sizes.__getitem__) if sizes else (0.0, 400)


def _pdf_page_blocks(
    lines: list[_PdfLine], page: int, body: tuple[float, int], section: str | None
) -> tuple[list[Block], str | None]:
    body_size, body_weight = body
    width = max((line.right for line in lines), default=0.0)
    blocks: list[Block] = []
    paragraph: list[str] = []
    heading: list[_PdfLine] = []

    for line in lines:
        bigger = line.size >= body_size * 1.15
        bolder = line.weight >= 600 > body_weight
        if (bigger or bolder) and len(line.text) <= TITLE_MAX:
            if text := _join(paragraph):
                blocks.append(Block(text, section, page))
            paragraph = []
            # Інший шрифт — інший заголовок; той самий — перенос довгого заголовка
            if heading and (heading[-1].size, heading[-1].weight) != (line.size, line.weight):
                section = " ".join(h.text for h in heading)
                blocks.append(Block(section, section, page, heading=True))
                heading = []
            heading.append(line)
            continue
        if heading:
            section = " ".join(h.text for h in heading)
            blocks.append(Block(section, section, page, heading=True))
            heading = []
        paragraph.append(line.text)
        if line.text.endswith(tuple(".!?…:")) and line.right < width * 0.85:
            blocks.append(Block(_join(paragraph), section, page))
            paragraph = []
    if heading:
        section = " ".join(h.text for h in heading)
        blocks.append(Block(section, section, page, heading=True))
    if text := _join(paragraph):
        blocks.append(Block(text, section, page))
    return blocks, section


def _title(blocks: list[Block]) -> str | None:
    """Назва (D-17): перший заголовок на початку документа або короткий перший рядок."""
    for block in blocks[:3]:
        if block.heading and len(block.text) <= TITLE_MAX:
            return block.text
    if blocks and len(first := blocks[0].text.split("\n")[0]) <= TITLE_MAX:
        return first if not first.endswith(".") else None
    return None


def _document(blocks: list[Block], **extra: object) -> ParsedDocument:
    return ParsedDocument(blocks=tuple(blocks), title=_title(blocks), **extra)  # type: ignore[arg-type]


# --- TXT і MD ---


def decode_text(data: bytes) -> str:
    """BOM → utf-8 → cp1251: старі українські .txt із Windows — у cp1251."""
    for bom, encoding in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(encoding)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def parse_text(data: bytes) -> ParsedDocument:
    blocks, _ = blocks_from_lines(decode_text(data).splitlines())
    return _document(blocks)


# --- PDF: текстовий шар, інакше OCR сторінки ---


def parse_pdf(data: bytes) -> ParsedDocument:
    """Текстовий шар → блоки за шрифтом; сторінка без тексту — скан → OCR.

    Під замком pdfium лише читання й рендер; OCR — уже без замка, тож кілька
    документів розпізнаються паралельно.
    """
    pages: list[list[_PdfLine] | np.ndarray] = []
    with _PDFIUM:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            raise ParseError("PDF пошкоджений або захищений паролем") from None
        try:
            for page in pdf:
                lines = _pdf_lines(page)
                if sum(len(line.text) for line in lines) >= MIN_PAGE_CHARS:
                    pages.append(lines)
                elif sum(isinstance(p, np.ndarray) for p in pages) >= OCR_MAX_PAGES:
                    raise ParseError(
                        f"Сканів понад {OCR_MAX_PAGES} сторінок не читаю — розділи документ"
                    )
                else:
                    pages.append(_render(page))
        finally:
            pdf.close()

    body = _body_font([p for p in pages if isinstance(p, list)])
    blocks: list[Block] = []
    section: str | None = None
    for number, page_content in enumerate(pages, start=1):
        if isinstance(page_content, list):
            page_blocks, section = _pdf_page_blocks(page_content, number, body, section)
        else:
            page_blocks, section = blocks_from_lines(
                ocr(page_content).splitlines(), page=number, section=section
            )
        blocks.extend(page_blocks)
    ocr_pages = sum(isinstance(p, np.ndarray) for p in pages)
    return _document(blocks, ocr_pages=ocr_pages, pages=len(pages))


def _scan_dpi(page: pdfium.PdfPage) -> float:
    """Роздільність найбільшого зображення на сторінці — це і є скан."""
    best = 0.0
    for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
        width_px, _ = obj.get_px_size()
        left, _, right, _ = obj.get_bounds()
        if right > left:
            best = max(best, width_px / ((right - left) / 72))
    return min(max(best or OCR_DPI_DEFAULT, OCR_DPI_MIN), OCR_DPI_MAX)


def _render(page: pdfium.PdfPage) -> np.ndarray:
    bitmap = page.render(scale=_scan_dpi(page) / 72, grayscale=True)
    pixels: np.ndarray = bitmap.to_numpy()
    return pixels.reshape(pixels.shape[0], pixels.shape[1])


def ocr(pixels: np.ndarray) -> str:
    """Сіре зображення → текст: поріг прибирає плями й тіні, далі tesseract (ukr + eng)."""
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        raise ParseError("Це скан, а розпізнавання тексту (tesseract) не встановлено")
    binary = np.where(pixels < OCR_THRESHOLD, 0, 255).astype(np.uint8)
    height, width = binary.shape
    # PGM — найпростіший формат, який tesseract читає зі stdin; Pillow не потрібен
    image = f"P5 {width} {height} 255\n".encode() + binary.tobytes()
    try:
        result = subprocess.run(  # noqa: S603 — фіксована програма, без shell
            [tesseract, "stdin", "stdout", "-l", OCR_LANGS],
            input=image,
            capture_output=True,
            timeout=OCR_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ParseError("Скан розпізнається задовго — спробуй файл із текстом") from None
    if result.returncode != 0:
        logger.warning("tesseract: %s", result.stderr.decode(errors="replace")[:300])
        raise ParseError("Не вдалося розпізнати скан")
    return _clean_ocr(result.stdout.decode("utf-8", errors="replace"))


def _clean_ocr(text: str) -> str:
    """Рядки-сміття від плям і рамок: без жодної літери або з однієї-двох літер серед знаків."""
    lines = []
    for line in text.splitlines():
        letters = sum(ch.isalpha() for ch in line)
        if line.strip() and letters < max(2, len(line.strip()) // 3):
            continue
        lines.append(line)
    return "\n".join(lines)


# --- DOCX і ODT: zip + XML ---


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise ParseError("Файл пошкоджений: немає вмісту документа") from None
    if info.file_size > MAX_XML_BYTES:
        raise ParseError("Документ завеликий для розбору")
    return archive.read(info)


def _open_zip(data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseError("Файл пошкоджений або це не той формат, що в назві") from None


def _xml(data: bytes) -> Element:
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError:
        raise ParseError("Файл пошкоджений: XML не читається") from None
    except DefusedXmlException:
        # Сутності й DTD у документі Word не потрібні — це спроба XML-бомби
        raise ParseError("Файл містить небезпечну XML-конструкцію") from None


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_styles(archive: zipfile.ZipFile) -> dict[str, str]:
    """id стилю → назва: у локалізованому Word «Заголовок 1» має id «1» чи «a3»."""
    if "word/styles.xml" not in archive.namelist():
        return {}
    root = _xml(_read_member(archive, "word/styles.xml"))
    styles = {}
    for style in root.iter(f"{W}style"):
        name = style.find(f"{W}name")
        if name is not None:
            styles[style.get(f"{W}styleId", "")] = name.get(f"{W}val", "").casefold()
    return styles


def _docx_text(node: Element) -> str:
    parts = []
    for el in node.iter():
        if el.tag == f"{W}t" and el.text:
            parts.append(el.text)
        elif el.tag == f"{W}tab":
            parts.append("\t")
        elif el.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def parse_docx(data: bytes) -> ParsedDocument:
    with _open_zip(data) as archive:
        styles = _docx_styles(archive)
        body = _xml(_read_member(archive, "word/document.xml")).find(f"{W}body")
    blocks: list[Block] = []
    section: str | None = None
    for node in body if body is not None else ():
        if node.tag == f"{W}p":
            text = _docx_text(node)
            if not text:
                continue
            style = node.find(f"{W}pPr/{W}pStyle")
            name = styles.get(style.get(f"{W}val", ""), "") if style is not None else ""
            outline = node.find(f"{W}pPr/{W}outlineLvl")
            if _HEADING_STYLE.match(name) or outline is not None:
                section = text
                blocks.append(Block(text, section, heading=True))
            else:
                bullet = node.find(f"{W}pPr/{W}numPr") is not None
                blocks.append(Block(f"– {text}" if bullet else text, section))
        elif node.tag == f"{W}tbl":
            rows = [
                " | ".join(_docx_text(cell) for cell in row.iter(f"{W}tc"))
                for row in node.iter(f"{W}tr")
            ]
            if table := "\n".join(r for r in rows if r.strip(" |")):
                blocks.append(Block(table, section))
    return _document(_merge_paragraphs(blocks))


TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


def _odt_text(node: Element) -> str:
    parts: list[str] = []

    def walk(el: Element) -> None:
        if el.text:
            parts.append(el.text)
        for child in el:
            if child.tag == f"{TEXT}s":
                parts.append(" " * int(child.get(f"{TEXT}c", "1")))
            elif child.tag == f"{TEXT}tab":
                parts.append("\t")
            elif child.tag == f"{TEXT}line-break":
                parts.append("\n")
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(node)
    return "".join(parts).strip()


STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"


def _odt_heading_styles(*roots: Element) -> set[str]:
    """Стилі абзаців, що походять від «Title»/«Heading» (у локалізованому Word — «Назва»)."""
    parents = {
        style.get(f"{STYLE}name", ""): style.get(f"{STYLE}parent-style-name", "")
        for root in roots
        for style in root.iter(f"{STYLE}style")
    }

    def is_heading(name: str, depth: int = 0) -> bool:
        if _HEADING_STYLE.match(name.replace("_20_", " ")):
            return True
        parent = parents.get(name)
        # Глибина — від циклу в стилях пошкодженого файлу
        return parent is not None and parent != "" and depth < 10 and is_heading(parent, depth + 1)

    return {name for name in parents if is_heading(name)}


def parse_odt(data: bytes) -> ParsedDocument:
    with _open_zip(data) as archive:
        root = _xml(_read_member(archive, "content.xml"))
        styles = (
            _xml(_read_member(archive, "styles.xml"))
            if "styles.xml" in archive.namelist()
            else root
        )
    headings = _odt_heading_styles(root, styles)
    blocks: list[Block] = []
    section: str | None = None

    def walk(node: Element, bullet: bool = False) -> None:
        nonlocal section
        for child in node:
            styled = child.get(f"{TEXT}style-name", "") in headings
            if (child.tag == f"{TEXT}h" or (child.tag == f"{TEXT}p" and styled)) and (
                text := _odt_text(child)
            ):
                section = text
                blocks.append(Block(text, section, heading=True))
            elif child.tag == f"{TEXT}p" and (text := _odt_text(child)):
                blocks.append(Block(f"– {text}" if bullet else text, section))
            elif child.tag == f"{TEXT}list":
                walk(child, bullet=True)
            elif child.tag == f"{TABLE}table":
                rows = [
                    " | ".join(_odt_text(cell) for cell in row.iter(f"{TABLE}table-cell"))
                    for row in child.iter(f"{TABLE}table-row")
                ]
                if table := "\n".join(r for r in rows if r.strip(" |")):
                    blocks.append(Block(table, section))
            else:
                walk(child, bullet)

    walk(root)
    return _document(_merge_paragraphs(blocks))


def _merge_paragraphs(blocks: list[Block]) -> list[Block]:
    """Пункти списку поспіль — один блок: так список не розсипається по фрагментах."""
    merged: list[Block] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and not previous.heading
            and not block.heading
            and previous.section == block.section
            and block.text.startswith("– ")
            and previous.text.lstrip().startswith("– ")
        ):
            merged[-1] = Block(previous.text + "\n" + block.text, block.section)
        else:
            merged.append(block)
    return merged


# --- Старі формати: .doc (antiword) і .rtf ---


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ParseError(f"Для цього формату потрібна програма {name}, а її не встановлено")
    return path


def parse_doc(data: bytes) -> ParsedDocument:
    antiword = _tool("antiword")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "document.doc"
        path.write_bytes(data)
        try:
            result = subprocess.run(  # noqa: S603 — фіксована програма, без shell
                [antiword, "-w", "0", "-m", "UTF-8.txt", str(path)],
                capture_output=True,
                timeout=TOOL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ParseError("Документ .doc читається задовго") from None
    if result.returncode != 0:
        logger.warning("antiword: %s", result.stderr.decode(errors="replace")[:300])
        raise ParseError("Не вдалося прочитати .doc — збережи його як .docx і надішли ще раз")
    blocks, _ = blocks_from_lines(result.stdout.decode("utf-8", errors="replace").splitlines())
    return _document(blocks)


_ANSICPG = re.compile(rb"\\ansicpg(\d+)")


def parse_rtf(data: bytes) -> ParsedDocument:
    match = _ANSICPG.search(data[:4096])
    encoding = f"cp{match.group(1).decode()}" if match else "cp1252"
    try:
        text: str = rtf_to_text(  # type: ignore[no-untyped-call]
            data.decode("latin-1"), encoding=encoding, errors="replace"
        )
    except (LookupError, ValueError):
        raise ParseError("RTF пошкоджений") from None
    # У RTF абзац — це \par, тобто один рядок; порожніх рядків між ними немає
    blocks, _ = blocks_from_lines(line for raw in text.splitlines() for line in (raw, ""))
    return _document(blocks)


_PARSERS: dict[str, Callable[[bytes], ParsedDocument]] = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".odt": parse_odt,
    ".doc": parse_doc,
    ".rtf": parse_rtf,
    ".txt": parse_text,
    ".md": parse_text,
}
