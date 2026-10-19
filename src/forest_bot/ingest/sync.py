"""Синхронізація «Лісового архіву» з Диском (D-40): на старті й кожні ~15 хвилин.

- Новий файл у теці → документ. Змінилась позначка зміни → завантажити й порівняти
  sha256: інший → нова версія тієї самої лінії; той самий → лише запам'ятати позначку.
- Файл зник із теки → його чинна архівна версія перестає діяти. Лише після повного
  успішного лістингу; порожня тека при відомих файлах — підозріла, нічого не прибираємо.
- Заміну з Telegram не відкочуємо: незмінний файл нічого не робить, а зниклий прибирає
  лише архівну версію, не звірину.
- Диск недоступний → працюємо з останньою копією (знімок і вже підтягнуте).
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import jobs
from forest_bot.archive.drive import DriveError, DriveFile
from forest_bot.ingest.archive import DRIVE_PREFIX, ArchiveEntry, known_files, write_entry
from forest_bot.ingest.index import PassageEmbedder
from forest_bot.ingest.parse import ParseError, parse
from forest_bot.ingest.place import detect_place
from forest_bot.llm.client import GroqClient
from forest_bot.models import Document
from forest_bot.places import PlaceGraph

logger = logging.getLogger(__name__)

ARCHIVE_SYNC = "archive_sync"


class Drive(Protocol):
    async def list_folder(self, folder_id: str) -> list[DriveFile]: ...

    async def download(self, file_id: str) -> bytes: ...


@dataclass
class SyncStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: list[str] = field(default_factory=list)


@dataclass
class DriveSync:
    """Задача черги: синхронізує й ставить у чергу наступну через `interval` секунд."""

    sessions: async_sessionmaker[AsyncSession]
    drive: Drive
    folder_id: str
    drive_dir: Path
    embedder: PassageEmbedder
    graph: PlaceGraph
    llm: GroqClient | None = None
    interval: float = 900.0

    async def __call__(self, payload: dict[str, Any]) -> None:
        try:
            await self.sync()
        except Exception:
            # Не пробуємо вдруге: наступна синхронізація й так за `interval`, а повтор
            # задачі поставив би в чергу ще одну — і їх ставало б дедалі більше
            logger.exception("Синхронізація архіву не вдалася")
        finally:
            async with self.sessions.begin() as session:
                await jobs.enqueue(session, ARCHIVE_SYNC, delay=self.interval)

    async def sync(self) -> SyncStats | None:
        try:
            files = await self.drive.list_folder(self.folder_id)
        except DriveError as error:
            logger.warning("Диск недоступний, працюю з останньою копією архіву: %s", error)
            return None
        stats = SyncStats()
        async with self.sessions() as session:
            known = await known_files(session)
        for file in files:
            await self._file(file, known.get(file.id), stats)

        listed = {f.id for f in files}
        gone = [d for i, d in known.items() if i not in listed and d.deleted_at is None]
        if not files and gone:
            logger.warning("Диск віддав порожню теку — нічого не прибираю")
        elif gone:
            stats.removed = await self._withdraw(gone)

        for reason in stats.skipped:
            logger.warning("Синхронізація, пропущено: %s", reason)
        if stats.added or stats.updated or stats.removed:
            logger.info(
                "Синхронізація архіву: нових %d, оновлено %d, зникли %d, без змін %d",
                stats.added,
                stats.updated,
                stats.removed,
                stats.unchanged,
            )
        return stats

    async def _file(self, file: DriveFile, previous: Document | None, stats: SyncStats) -> None:
        # Зниклий і знову з'явлений файл — нова версія, навіть якщо вміст той самий
        alive = previous if previous is not None and previous.deleted_at is None else None
        # Позначка та сама — файл не чіпали, качати не треба
        if alive is not None and alive.archive_modified == file.modified:
            stats.unchanged += 1
            return
        try:
            data = await self.drive.download(file.id)
        except DriveError as error:
            stats.skipped.append(f"{file.name}: {error}")
            return
        sha = hashlib.sha256(data).hexdigest()
        if alive is not None and alive.file_sha256 == sha:
            await self._remember(alive, file.modified)
            stats.unchanged += 1
            return
        if await self._import(file, data, sha, previous, stats):
            if previous is None:
                stats.added += 1
            else:
                stats.updated += 1

    async def _import(
        self,
        file: DriveFile,
        data: bytes,
        sha: str,
        previous: Document | None,
        stats: SyncStats,
    ) -> bool:
        try:
            parsed = await asyncio.to_thread(parse, data, file.name)
        except ParseError as error:
            stats.skipped.append(f"{file.name}: {error}")
            return False
        decision = await detect_place(parsed, self.graph, self.llm)
        if decision.place_id is None:
            stats.skipped.append(f"{file.name}: місце не визначено")
            return False
        # Ім'я на диску — з id і sha: версії одного файлу не перезаписують одна одну
        name = f"{file.id}-{sha[:12]}{PurePath(file.name).suffix.lower()}"
        await asyncio.to_thread(_save, self.drive_dir / name, data)
        entry = ArchiveEntry(
            id=file.id,
            name=file.name,
            file=DRIVE_PREFIX + name,
            modified=file.modified,
            mime=file.mime,
            sha256=sha,
            size=len(data),
        )
        try:
            await write_entry(
                self.sessions, entry, parsed, decision.place_id, previous, self.embedder, self.graph
            )
        except IntegrityError:
            # Той самий файл уже надіслав звір через Telegram (A-09)
            stats.skipped.append(f"{file.name}: такий файл уже є в боті")
            return False
        return True

    async def _remember(self, document: Document, modified: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(Document).where(Document.id == document.id).values(archive_modified=modified)
            )

    async def _withdraw(self, gone: list[Document]) -> int:
        """Зниклі з теки: чинна архівна версія перестає діяти. Не чинна (її замінив звір
        через Telegram) лишається як є — звіриний варіант діє далі."""
        removed = 0
        async with self.sessions.begin() as session:
            for known in gone:
                document = await session.get(Document, known.id, with_for_update=True)
                if document is None or not document.is_current:
                    continue
                document.is_current = False
                document.deleted_at = datetime.now(UTC)
                removed += 1
                logger.info("Архів: «%s» зник із теки — більше не діє", document.title)
        return removed


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
