"""Імпорт архіву: нові документи з місцем, повторний старт без змін, змінений файл — нова версія."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot.ingest.archive import MANIFEST, import_archive
from forest_bot.ingest.index import index_document
from forest_bot.models import Document, DocumentSource, JoinedVia
from forest_bot.places import load_places
from forest_bot.repo import documents, users
from forest_bot.repo.documents import FileInfo
from forest_bot.repo.places import sync_places
from tests.integration.test_index import PARSED, FakeEmbedder

pytestmark = pytest.mark.integration

GRAPH = load_places()
ARCHIVE = Path(__file__).resolve().parents[2] / "data" / "archive"
# Текстові документи (без OCR): рукавичка, частина, увесь ліс
NAMES = (
    "Членські внески рукавички Під дубом.txt",
    "Небезпеки біля річки.txt",
    "Календар сезонів.txt",
)
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    manifest = json.loads((ARCHIVE / MANIFEST).read_text(encoding="utf-8"))
    files = [f for f in manifest["files"] if f["file"] in NAMES]
    for f in files:
        shutil.copy(ARCHIVE / f["file"], tmp_path / f["file"])
    (tmp_path / MANIFEST).write_text(json.dumps({"files": files}), encoding="utf-8")
    return tmp_path


def change(folder: Path, name: str, text: str) -> None:
    """Мишка оновила файл на Диску: нові байти й sha256 у маніфесті."""
    data = text.encode()
    (folder / name).write_bytes(data)
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    for f in manifest["files"]:
        if f["file"] == name:
            f["sha256"], f["size"] = hashlib.sha256(data).hexdigest(), len(data)
    (folder / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")


async def current(sessions: Sessions) -> dict[str, Document]:
    async with sessions() as session:
        rows = await session.scalars(select(Document).where(Document.is_current))
        return {d.file_name: d for d in rows}


@pytest.fixture
async def places(sessions: Sessions) -> None:
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)


@pytest.mark.usefixtures("places")
async def test_first_start_imports_with_places(sessions: Sessions, folder: Path) -> None:
    # R-19: архів у боті з першого старту, без Диска
    stats = await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    assert (stats.added, stats.updated, stats.unchanged, stats.skipped) == (3, 0, 0, [])
    docs = await current(sessions)
    assert {name: d.place_id for name, d in docs.items()} == {
        "Членські внески рукавички Під дубом.txt": "pid-dubom",
        "Небезпеки біля річки.txt": "river",
        "Календар сезонів.txt": "forest",
    }
    fees = docs["Членські внески рукавички Під дубом.txt"]
    assert fees.title == "Членські внески рукавички «Під дубом»"
    assert (fees.source, fees.sender_id) == (DocumentSource.ARCHIVE, None)


@pytest.mark.usefixtures("places")
async def test_restart_changes_nothing(sessions: Sessions, folder: Path) -> None:
    await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    embedder = FakeEmbedder()
    stats = await import_archive(sessions, folder, embedder, GRAPH)
    assert (stats.added, stats.updated, stats.unchanged) == (0, 0, 3)
    assert embedder.seen == []


@pytest.mark.usefixtures("places")
async def test_changed_file_is_a_new_version(sessions: Sessions, folder: Path) -> None:
    await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    before = (await current(sessions))["Небезпеки біля річки.txt"]
    change(
        folder,
        "Небезпеки біля річки.txt",
        "Небезпеки\n\nДля всіх, хто живе біля річки. Брід закрито.",
    )

    stats = await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    assert (stats.added, stats.updated, stats.unchanged) == (0, 1, 2)
    after = (await current(sessions))["Небезпеки біля річки.txt"]
    assert (after.lineage_id, after.version) == (before.lineage_id, 2)


@pytest.mark.usefixtures("places")
async def test_unchanged_archive_does_not_revert_telegram_replacement(
    sessions: Sessions, folder: Path
) -> None:
    await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    archived = (await current(sessions))["Календар сезонів.txt"]
    # Сова надіслала новий варіант календаря й підтвердила заміну (R-15, D-37)
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

    stats = await import_archive(sessions, folder, FakeEmbedder(), GRAPH)
    assert stats.unchanged == 3
    assert (await current(sessions))["kalendar.txt"].id == replacement.id
