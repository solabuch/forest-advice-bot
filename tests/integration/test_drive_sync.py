"""Синхронізація архіву з Диском (D-40): новий, змінений, зниклий файл, збій лістингу;
заміна з Telegram і версія з Диска не відкочуються; задача сама себе планує."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from forest_bot.archive.drive import DriveError, DriveFile
from forest_bot.ingest.archive import MANIFEST, import_archive
from forest_bot.ingest.index import index_document
from forest_bot.ingest.sync import ARCHIVE_SYNC, DriveSync
from forest_bot.models import Document, DocumentSource, Job, JoinedVia
from forest_bot.repo import documents, users
from forest_bot.repo.documents import FileInfo
from tests.factories import make_settings
from tests.integration.test_archive_import import GRAPH, Sessions, current, folder, places
from tests.integration.test_index import PARSED, FakeEmbedder

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("places")]

__all__ = ["folder", "places"]

RIVER = "Небезпеки біля річки.txt"
CALENDAR = "Календар сезонів.txt"
MUSHROOMS = "Гриби на узліссі.txt"
MUSHROOMS_TEXT = (
    "Гриби на узліссі\n\nДля мешканців рукавички «На узліссі». Лисички збирайте після дощу."
)


class FakeDrive:
    """Тека Диска в пам'яті: файли за id, лічильник завантажень, збій лістингу на вимогу."""

    def __init__(self, folder: Path) -> None:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        self.files: dict[str, tuple[DriveFile, bytes]] = {
            f["id"]: (
                DriveFile(f["id"], f["name"], f["modified"], f["mime"]),
                (folder / f["file"]).read_bytes(),
            )
            for f in manifest["files"]
        }
        self.downloads: list[str] = []
        self.broken: Exception | None = None

    def id_of(self, name: str) -> str:
        return next(i for i, (f, _) in self.files.items() if f.name == name)

    def put(self, file_id: str, name: str, modified: str, data: bytes) -> None:
        self.files[file_id] = (DriveFile(file_id, name, modified, "text/plain"), data)

    async def list_folder(self, folder_id: str) -> list[DriveFile]:
        if self.broken:
            raise self.broken
        return [f for f, _ in self.files.values()]

    async def download(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        return self.files[file_id][1]


@pytest.fixture
async def drive(sessions: Sessions, folder: Path) -> FakeDrive:
    """Бот уже імпортував знімок; на Диску — ті самі файли."""
    await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    return FakeDrive(folder)


def syncer(sessions: Sessions, drive: FakeDrive, tmp_path: Path) -> DriveSync:
    return DriveSync(sessions, drive, "folder", tmp_path / "drive", FakeEmbedder(), GRAPH)


async def test_same_folder_downloads_nothing(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    stats = await syncer(sessions, drive, tmp_path).sync()
    assert stats is not None
    assert (stats.added, stats.updated, stats.removed, stats.unchanged) == (0, 0, 0, 3)
    assert drive.downloads == []


async def test_new_file_is_added_and_can_be_sent(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    drive.put("new-id", MUSHROOMS, "Oct 20", MUSHROOMS_TEXT.encode())
    stats = await syncer(sessions, drive, tmp_path).sync()
    assert stats is not None
    assert stats.added == 1
    document = (await current(sessions))[MUSHROOMS]
    assert (document.place_id, document.source) == ("na-uzlissi", DocumentSource.ARCHIVE)
    # Файл — у томі, кнопка «Документ» (D-28) його знайде
    assert document.storage_path is not None
    path = make_settings(drive_dir=tmp_path / "drive").archive_file(document.storage_path)
    assert path.read_bytes() == MUSHROOMS_TEXT.encode()


async def test_changed_file_is_a_new_version(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    before = (await current(sessions))[RIVER]
    text = "Небезпеки біля річки\n\nДля всіх, хто живе біля річки. Брід закрито до весни."
    drive.put(drive.id_of(RIVER), RIVER, "Oct 20", text.encode())
    stats = await syncer(sessions, drive, tmp_path).sync()
    assert stats is not None
    assert stats.updated == 1
    after = (await current(sessions))[RIVER]
    assert (after.lineage_id, after.version) == (before.lineage_id, 2)


async def test_touched_file_with_same_content_is_only_remembered(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    file_id = drive.id_of(RIVER)
    drive.put(file_id, RIVER, "Oct 20", drive.files[file_id][1])
    sync = syncer(sessions, drive, tmp_path)
    await sync.sync()
    assert drive.downloads == [file_id]
    assert (await current(sessions))[RIVER].version == 1
    # Позначку запам'ятали — наступного разу не качаємо
    await sync.sync()
    assert drive.downloads == [file_id]


async def test_vanished_file_is_no_longer_used(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    file_id = drive.id_of(RIVER)
    data = drive.files.pop(file_id)[1]
    stats = await syncer(sessions, drive, tmp_path).sync()
    assert stats is not None
    assert stats.removed == 1
    assert set(await current(sessions)) == {CALENDAR, "Членські внески рукавички Під дубом.txt"}

    # Повернувся в теку — знову діє
    drive.put(file_id, RIVER, "Oct 21", data)
    await syncer(sessions, drive, tmp_path).sync()
    assert RIVER in await current(sessions)


async def test_broken_listing_changes_nothing(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    drive.broken = DriveError("HTTP 503")
    assert await syncer(sessions, drive, tmp_path).sync() is None
    assert len(await current(sessions)) == 3


async def test_empty_listing_removes_nothing(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    drive.files.clear()
    await syncer(sessions, drive, tmp_path).sync()
    assert len(await current(sessions)) == 3


async def test_vanished_file_keeps_telegram_replacement(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    archived = (await current(sessions))[CALENDAR]
    async with sessions.begin() as session:
        owl = await users.admit(session, 2001, JoinedVia.INVITE)
        await users.set_name(session, owl, "Сова")
        replacement = await documents.create(
            session,
            place_id="forest",
            title="Календар сезонів",
            source=DocumentSource.TELEGRAM,
            sender_id=owl.id,
            file=FileInfo(file_name="kalendar.txt", size_bytes=1, file_sha256="f" * 64),
            lineage_id=archived.lineage_id,
        )
        await index_document(session, replacement, PARSED, FakeEmbedder(), GRAPH)
    drive.files.pop(drive.id_of(CALENDAR))
    await syncer(sessions, drive, tmp_path).sync()
    assert (await current(sessions))["kalendar.txt"].id == replacement.id


async def test_snapshot_does_not_revert_drive_version(
    sessions: Sessions, drive: FakeDrive, folder: Path, tmp_path: Path
) -> None:
    text = "Небезпеки біля річки\n\nДля всіх, хто живе біля річки. Брід закрито до весни."
    drive.put(drive.id_of(RIVER), RIVER, "Oct 20", text.encode())
    await syncer(sessions, drive, tmp_path).sync()
    # Рестарт: знімок в образі старіший за Диск
    stats = await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    assert (stats.added, stats.updated) == (0, 0)
    assert (await current(sessions))[RIVER].version == 2


async def test_job_plans_the_next_sync_even_after_a_failure(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    drive.broken = RuntimeError("щось несподіване")
    sync = syncer(sessions, drive, tmp_path)
    sync.interval = 900
    await sync({})
    async with sessions() as session:
        [job] = await session.scalars(select(Job).where(Job.kind == ARCHIVE_SYNC))
    later = datetime.now(UTC) + timedelta(seconds=850)
    assert job.run_after > later


async def test_withdrawn_document_is_not_in_search(
    sessions: Sessions, drive: FakeDrive, tmp_path: Path
) -> None:
    river = (await current(sessions))[RIVER]
    drive.files.pop(drive.id_of(RIVER))
    await syncer(sessions, drive, tmp_path).sync()
    async with sessions() as session:
        document = await session.get_one(Document, river.id)
    assert not document.is_current
    assert document.deleted_at is not None
