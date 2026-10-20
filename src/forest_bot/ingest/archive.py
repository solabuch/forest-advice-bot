"""Імпорт «Лісового архіву» зі знімка (R-19, D-40, R-15).

- Новий файл → документ у новій лінії.
- Змінений файл (інший sha256) → нова версія тієї самої лінії.
- Незмінений → нічого: заміна, яку звір надіслав через Telegram, не відкочується —
  порівнюємо з останньою архівною версією, а не з чинною.
- Місце — за фразою адресата (78/78 без LLM); немає фрази — LLM; не впевнена —
  документ пропускаємо з попередженням: в архіву немає автора, якого можна спитати.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import notify
from forest_bot.ingest.index import PassageEmbedder, index_document
from forest_bot.ingest.parse import ParsedDocument, ParseError, parse
from forest_bot.ingest.place import detect_place
from forest_bot.ingest.translate import LlmTranslator
from forest_bot.llm.client import GroqClient
from forest_bot.models import Document, DocumentSource
from forest_bot.places import PlaceGraph
from forest_bot.repo import documents
from forest_bot.repo.documents import FileInfo

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"
# Файли, які синхронізація завантажила з Диска, лежать не в знімку, а в томі (DRIVE_DIR)
DRIVE_PREFIX = "drive/"
# OCR сканів — у зовнішньому процесі, тож два документи паралельно справді швидші
PARALLEL = 2


@dataclass(frozen=True)
class ArchiveEntry:
    id: str
    name: str
    file: str
    modified: str
    mime: str | None
    sha256: str
    size: int


@dataclass
class ImportStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)
    seconds: float = 0.0


def read_manifest(folder: Path) -> list[ArchiveEntry]:
    data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    return [
        ArchiveEntry(
            id=f["id"],
            name=f["name"],
            file=f["file"],
            modified=f.get("modified", ""),
            mime=f.get("mime"),
            sha256=f["sha256"],
            size=int(f["size"]),
        )
        for f in data["files"]
    ]


async def known_files(session: AsyncSession) -> dict[str, Document]:
    """Остання архівна версія кожного файлу Диска — і зі знімка, і з синхронізації."""
    rows = await session.scalars(
        select(Document)
        .where(Document.source == DocumentSource.ARCHIVE)
        .order_by(Document.archive_file_id, Document.version.desc())
        .ext(distinct_on(Document.archive_file_id))
    )
    return {doc.archive_file_id: doc for doc in rows if doc.archive_file_id}


def _snapshot_is_newer(entry: ArchiveEntry, known: Document | None) -> bool:
    """Файл знімка імпортуємо, якщо його ще немає або знімок оновили (новий образ).

    Версію, яку вже підтягнула синхронізація з Диска, і файл, що зник із теки, знімок
    не перекриває: інакше кожен рестарт відкочував би архів до стану на момент збірки.
    """
    if known is None:
        return True
    if known.deleted_at is not None or (known.storage_path or "").startswith(DRIVE_PREFIX):
        return False
    return known.file_sha256 != entry.sha256


async def import_archive(
    sessions: async_sessionmaker[AsyncSession],
    folder: Path,
    embedder: PassageEmbedder,
    graph: PlaceGraph,
    llm: GroqClient | None = None,
) -> ImportStats:
    started = time.monotonic()
    stats = ImportStats()
    entries = read_manifest(folder)
    async with sessions() as session:
        known = await known_files(session)
    todo = [e for e in entries if _snapshot_is_newer(e, known.get(e.id))]
    stats.unchanged = len(entries) - len(todo)

    slots = asyncio.Semaphore(PARALLEL)
    # Запис — по одному: документ шукається, щойно розібраний; скани не тримають решту
    writing = asyncio.Lock()

    async def handle(entry: ArchiveEntry) -> None:
        async with slots:
            data = await asyncio.to_thread((folder / entry.file).read_bytes)
            try:
                parsed = await asyncio.to_thread(parse, data, entry.file)
            except ParseError as error:
                stats.skipped.append(f"{entry.name}: {error}")
                return
        decision = await detect_place(parsed, graph, llm)
        if decision.place_id is None:
            stats.skipped.append(f"{entry.name}: місце не визначено")
            return
        async with writing:
            previous = known.get(entry.id)
            await write_entry(
                sessions, entry, parsed, decision.place_id, previous, embedder, graph, llm
            )
            if previous:
                stats.updated += 1
            else:
                stats.added += 1
            done = stats.added + stats.updated
            if done % 10 == 0:
                logger.info("Архів: проіндексовано %d із %d", done, len(todo))

    async def run(entry: ArchiveEntry) -> None:
        try:
            await handle(entry)
        except IntegrityError:
            # Той самий файл уже надіслав звір через Telegram (A-09)
            stats.skipped.append(f"{entry.name}: такий файл уже є в боті")

    # Спершу текстові формати: PDF може бути сканом, і OCR не має затримувати решту
    todo.sort(key=lambda e: e.mime == "application/pdf")
    await asyncio.gather(*(run(e) for e in todo))

    stats.seconds = time.monotonic() - started
    for reason in stats.skipped:
        logger.warning("Архів, пропущено: %s", reason)
    logger.info(
        "Архів: нових %d, оновлено %d, без змін %d, пропущено %d — %.1f с",
        stats.added,
        stats.updated,
        stats.unchanged,
        len(stats.skipped),
        stats.seconds,
    )
    return stats


async def write_entry(
    sessions: async_sessionmaker[AsyncSession],
    entry: ArchiveEntry,
    parsed: ParsedDocument,
    place_id: str,
    previous: Document | None,
    embedder: PassageEmbedder,
    graph: PlaceGraph,
    llm: GroqClient | None = None,
) -> None:
    translator = LlmTranslator(llm) if llm is not None else None
    async with sessions.begin() as session:
        document = await documents.create(
            session,
            place_id=place_id,
            title=parsed.title or PurePath(entry.name).stem,
            source=DocumentSource.ARCHIVE,
            file=FileInfo(
                file_name=entry.name,
                size_bytes=entry.size,
                file_sha256=entry.sha256,
                mime=entry.mime,
                storage_path=entry.file,
                archive_file_id=entry.id,
                archive_modified=entry.modified,
            ),
            lineage_id=previous.lineage_id if previous else None,
        )
        replaced = await index_document(session, document, parsed, embedder, graph, translator)
        await notify.enqueue(session, document, replaced=replaced is not None)
