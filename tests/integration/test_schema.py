"""Схема: синхронізація місць і інваріанти, які тримає сама БД."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Document, DocumentSource, DocumentStatus, PlacePart, PlaceRow, User
from forest_bot.places import PlaceGraph, load_places
from forest_bot.repo.places import sync_places

pytestmark = pytest.mark.integration


async def test_sync_places(session: AsyncSession) -> None:
    graph = load_places()
    await sync_places(session, graph)
    assert await session.scalar(select(func.count()).select_from(PlaceRow)) == 12
    parts = await session.scalars(
        select(PlacePart.part_id).where(PlacePart.mitten_id == "pid-verboiu")
    )
    assert sorted(parts) == ["deciduous", "river"]
    assert await session.scalar(select(func.count()).select_from(PlacePart)) == 8

    # Місце зникло з файлу → неактивне; повернулося → знову активне
    reduced = PlaceGraph(tuple(p for p in graph.places if p.id != "na-uzlissi"))
    await sync_places(session, reduced)
    assert (
        await session.scalar(select(PlaceRow.is_active).where(PlaceRow.id == "na-uzlissi")) is False
    )
    await sync_places(session, graph)
    assert (
        await session.scalar(select(PlaceRow.is_active).where(PlaceRow.id == "na-uzlissi")) is True
    )


def document(lineage: uuid.UUID, version: int, **overrides: object) -> Document:
    values: dict[str, object] = {
        "lineage_id": lineage,
        "version": version,
        "place_id": "pid-dubom",
        "title": "Членські внески",
        "title_key": "членські внески",
        "source": DocumentSource.ARCHIVE,
        "status": DocumentStatus.READY,
        "file_name": "vnesky.txt",
        "size_bytes": 10,
        "file_sha256": uuid.uuid4().hex,
    }
    values.update(overrides)
    return Document(**values)


@pytest.fixture
async def places(session: AsyncSession) -> None:
    await sync_places(session, load_places())


@pytest.mark.usefixtures("places")
async def test_one_current_version_per_lineage(session: AsyncSession) -> None:
    lineage = uuid.uuid4()
    session.add(document(lineage, 1, is_current=True))
    await session.flush()
    session.add(document(lineage, 2, is_current=True))
    with pytest.raises(IntegrityError, match="uq_documents_current_lineage"):
        await session.flush()


@pytest.mark.usefixtures("places")
async def test_current_version_must_be_ready(session: AsyncSession) -> None:
    session.add(document(uuid.uuid4(), 1, is_current=True, status=DocumentStatus.PROCESSING))
    with pytest.raises(IntegrityError, match="ck_documents_current_is_ready"):
        await session.flush()


@pytest.mark.usefixtures("places")
async def test_sender_matches_source(session: AsyncSession) -> None:
    session.add(User(id=42, real_name="Кабан", name_key="кабан"))
    await session.flush()
    session.add(document(uuid.uuid4(), 1, sender_id=42))
    with pytest.raises(IntegrityError, match="ck_documents_sender_matches_source"):
        await session.flush()


@pytest.mark.usefixtures("places")
async def test_same_file_not_added_twice(session: AsyncSession) -> None:
    session.add(document(uuid.uuid4(), 1, file_sha256="a" * 64))
    await session.flush()
    session.add(document(uuid.uuid4(), 1, file_sha256="a" * 64))
    with pytest.raises(IntegrityError, match="uq_documents_alive_sha256"):
        await session.flush()
