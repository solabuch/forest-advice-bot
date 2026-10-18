"""Парсери: усі формати D-14, назва (D-17), розділи й сторінки (D-18), захист від поганих файлів.

Фікстури — справжні документи «Лісового архіву» і їхні копії в .doc/.rtf/.odt/cp1251/.md
(згенеровано з архівного .docx через Word). Тести, яким потрібні tesseract чи antiword,
локально без програм пропускаються, а в CI падають.
"""

import io
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from forest_bot.ingest import parse as parse_module
from forest_bot.ingest.parse import ParsedDocument, ParseError, decode_text, parse

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data" / "archive"
FORMATS = ROOT / "tests" / "fixtures" / "formats"

STATUT_HEADINGS = ["Статут ОСМД «Під вербою»", "Положення статуту", "Наостанок"]


def archive(name: str) -> ParsedDocument:
    return parse((ARCHIVE / name).read_bytes(), name)


def fixture(name: str) -> ParsedDocument:
    return parse((FORMATS / name).read_bytes(), name)


def headings(document: ParsedDocument) -> list[str]:
    return [b.text for b in document.blocks if b.heading]


def require(tool: str) -> None:
    if shutil.which(tool) is None:
        if os.environ.get("CI"):
            pytest.fail(f"{tool} не встановлено в CI")
        pytest.skip(f"немає {tool}")


# --- Текстові формати ---


def test_txt_structure_from_archive() -> None:
    document = archive("Календар сезонів.txt")
    assert document.title == "Календар сезонів"
    assert headings(document)[:3] == ["Календар сезонів", "Серпень, вересень, жовтень", "Жовтень"]
    october = next(b for b in document.blocks if b.text.startswith("Жолуді"))
    assert october.section == "Жовтень"
    assert october.page is None


@pytest.mark.parametrize("name", ["vnesky-cp1251.txt", "vnesky.md"])
def test_cp1251_and_markdown_match_utf8(name: str) -> None:
    original = archive("Членські внески рукавички Під дубом.txt")
    document = fixture(name)
    assert document.title == original.title == "Членські внески рукавички «Під дубом»"
    assert [b.text for b in document.blocks] == [b.text for b in original.blocks]


@pytest.mark.parametrize(
    ("data", "text"),
    [
        ("Жолуді".encode(), "Жолуді"),
        ("Жолуді".encode("cp1251"), "Жолуді"),
        (b"\xef\xbb\xbf" + "Жолуді".encode(), "Жолуді"),
        ("Жолуді".encode("utf-16"), "Жолуді"),
    ],
    ids=["utf-8", "cp1251", "utf-8-bom", "utf-16"],
)
def test_decode_text(data: bytes, text: str) -> None:
    assert decode_text(data) == text


def test_list_items_stay_on_their_lines() -> None:
    document = parse("Основне\n\n- Внесок — 4 жолуді.\n- До 10 числа.\n".encode(), "x.txt")
    assert document.blocks[1].text == "- Внесок — 4 жолуді.\n- До 10 числа."


def test_wrapped_line_is_joined() -> None:
    document = parse("Гриби шукайте після\nдощу, коли земля во-\nлога.".encode(), "x.txt")
    assert document.blocks[0].text == "Гриби шукайте після дощу, коли земля волога."


# --- Word і OpenDocument ---


@pytest.mark.parametrize("name", ["statut.odt", "statut.rtf"])
def test_converted_formats_match_docx(name: str) -> None:
    original = archive("Статут ОСМД Під вербою.docx")
    document = fixture(name)
    assert headings(original) == headings(document) == STATUT_HEADINGS
    assert document.title == original.title == STATUT_HEADINGS[0]
    assert "Голова ОСМД — Качка." in document.text


def test_docx_sections_and_bullets() -> None:
    document = archive("Статут ОСМД Під вербою.docx")
    head = next(b for b in document.blocks if "Качка" in b.text)
    assert head.section == "Положення статуту"


def test_doc_via_antiword() -> None:
    require("antiword")
    document = fixture("statut.doc")
    assert document.title == STATUT_HEADINGS[0]
    assert "Скарбник ОСМД — Жабка." in document.text


# --- PDF ---


def test_pdf_title_spans_two_lines() -> None:
    # 22 pt, вага 800 — заголовок; перенесений рядок склеюється
    document = archive("Правила співжиття в рукавичці Біля бобрової греблі.pdf")
    assert document.title == "Правила співжиття в рукавичці «Біля бобрової греблі»"
    assert document.ocr_pages == 0
    assert document.pages == 1


def test_pdf_headings_by_font_and_paragraphs() -> None:
    document = archive("Їстівне й отруйне.pdf")
    assert headings(document) == [
        "Їстівне й отруйне",
        "Коли шукати гриби",
        "Бліда поганка",
        "Вовчі ягоди",
        "Головне правило",
    ]
    poganka = next(b for b in document.blocks if b.text.startswith("Бліда поганка схожа"))
    assert poganka.section == "Бліда поганка"
    assert poganka.page == 1
    # Перенос рядка всередині абзацу — пробіл, а не розрив
    assert "у блідої поганки є кільце на ніжці" in poganka.text


def test_scan_is_recognized() -> None:
    require("tesseract")
    document = archive("Членські внески рукавички У соснах.pdf")
    assert document.ocr_pages == 1
    assert document.title is not None
    assert "Членські внески" in document.title
    for fact in ("2 соснові шишки", "щонеділі", "Синиці", "1 морквина за 2 шишки"):
        assert fact in document.text


def test_scan_without_tesseract_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(ParseError, match="tesseract"):
        archive("Членські внески рукавички У соснах.pdf")


def test_whole_archive_parses() -> None:
    # Усі 78 документів: і текстові, і скани (OCR)
    require("tesseract")
    for path in sorted(p for p in ARCHIVE.iterdir() if p.name != "manifest.json"):
        document = parse(path.read_bytes(), path.name)
        assert document.title, path.name
        assert len(document.text) > 300, path.name


def test_ocr_noise_lines_are_dropped() -> None:
    noisy = "Заміна шишок\n~~ -- _ —\n|\nЗайчик може платити морквою."
    assert parse_module._clean_ocr(noisy) == "Заміна шишок\nЗайчик може платити морквою."


# --- Погані файли ---


def test_unsupported_format_lists_supported() -> None:
    with pytest.raises(ParseError, match=r"\.pdf, \.docx, \.doc"):
        parse(b"\x89PNG", "photo.png")


@pytest.mark.parametrize(
    ("data", "name", "message"),
    [
        (b"   \n\n  ", "empty.txt", "не знайшлося тексту"),
        (b"not a zip", "fake.docx", "пошкоджений"),
        (b"%PDF-1.4 broken", "broken.pdf", "PDF пошкоджений"),
    ],
    ids=["empty", "fake-docx", "broken-pdf"],
)
def test_bad_files(data: bytes, name: str, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        parse(data, name)


def docx_with(document_xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive_zip:
        archive_zip.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_xml_bomb_is_rejected() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><l>&b;</l>'
    )
    with pytest.raises(ParseError, match="небезпечну"):
        parse(docx_with(bomb), "bomb.docx")


def test_zip_bomb_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_module, "MAX_XML_BYTES", 100)
    with pytest.raises(ParseError, match="завеликий"):
        parse(docx_with(b"<w>" + b" " * 1000 + b"</w>"), "big.docx")


def test_missing_antiword_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(ParseError, match="antiword"):
        fixture("statut.doc")
