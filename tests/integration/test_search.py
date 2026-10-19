"""Гібридний пошук (ADR-05): область місця, чинні версії, слова з відмінками, small-to-big."""

import uuid
from collections.abc import Sequence

import numpy as np
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import EMBEDDING_DIM, Chunk, Document, DocumentSource
from forest_bot.places import load_places
from forest_bot.qa.search import WHOLE_DOCUMENT_CHARS, Passage, scope, search
from forest_bot.repo import documents
from forest_bot.repo.places import sync_places

pytestmark = pytest.mark.integration

GRAPH = load_places()


def axis(index: int) -> list[float]:
    """Одиничний вектор: близькість двох — 1 (та сама вісь) або 0 (різні)."""
    vector = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    vector[index] = 1.0
    return vector.tolist()


QUESTION = axis(0)


@pytest.fixture
async def forest(session: AsyncSession) -> AsyncSession:
    await sync_places(session, GRAPH)
    return session


async def add(
    session: AsyncSession,
    place_id: str,
    title: str,
    texts: Sequence[str] = ("Порада.",),
    *,
    vector: list[float] = QUESTION,
    lineage: Document | None = None,
) -> Document:
    document = await documents.create(
        session,
        place_id=place_id,
        title=title,
        source=DocumentSource.ARCHIVE,
        file=documents.FileInfo(f"{title}.txt", 10, uuid.uuid4().hex),
        lineage_id=lineage.lineage_id if lineage else None,
    )
    session.add_all(
        Chunk(
            document_id=document.id,
            ord=index,
            text=text,
            section=f"Розділ {index}",
            page=index + 1,
            embedding=vector,
        )
        for index, text in enumerate(texts)
    )
    await session.flush()
    await documents.activate(session, document)
    return document


async def find(session: AsyncSession, place_id: str, question: str = "порада") -> list[Passage]:
    return await search(session, [QUESTION], question, scope(place_id, GRAPH), GRAPH)


async def titles(session: AsyncSession, place_id: str) -> set[str]:
    return {p.title for p in await find(session, place_id)}


async def test_scope_follows_the_place_graph(forest: AsyncSession) -> None:
    for place_id in ("pid-dubom", "u-lishchyni", "deciduous", "coniferous", "forest"):
        await add(forest, place_id, place_id)
    # R-10: рукавичка — вона, її частина й ліс; сусідня рукавичка й чужа частина — ні
    assert await titles(forest, "pid-dubom") == {"pid-dubom", "deciduous", "forest"}
    # R-17: частина — без порад рукавичок
    assert await titles(forest, "deciduous") == {"deciduous", "forest"}
    # R-27: ліс — лише ліс
    assert await titles(forest, "forest") == {"forest"}


async def test_mitten_in_two_parts_gets_both(forest: AsyncSession) -> None:
    for place_id in ("deciduous", "river", "coniferous"):
        await add(forest, place_id, place_id)
    assert await titles(forest, "pid-verboiu") == {"deciduous", "river"}


async def test_replaced_version_is_not_found(forest: AsyncSession) -> None:
    old = await add(forest, "pid-dubom", "Внески 2025")
    await add(forest, "pid-dubom", "Внески 2026", lineage=old)
    assert await titles(forest, "pid-dubom") == {"Внески 2026"}


async def test_words_in_other_case_are_found(forest: AsyncSession) -> None:
    # Вектор далекий (близькість 0), але «жолудів» знаходить «4 жолуді»
    await add(forest, "pid-dubom", "Внески", ["Внесок — 4 жолуді на місяць."], vector=axis(1))
    await add(forest, "pid-dubom", "Стежки", ["Стежка до річки."], vector=axis(2))
    found = await find(forest, "pid-dubom", "Скільки жолудів платити?")
    assert [(p.title, p.keyword) for p in found] == [("Внески", True)]


async def test_far_and_wordless_is_not_found(forest: AsyncSession) -> None:
    await add(forest, "pid-dubom", "Стежки", ["Стежка до річки."], vector=axis(2))
    assert await find(forest, "pid-dubom", "Скільки коштує квиток?") == []


async def test_short_document_goes_whole(forest: AsyncSession) -> None:
    await add(forest, "pid-dubom", "Внески", ["Основне.", "Заміна.", "Хто не платить."])
    [passage] = await find(forest, "pid-dubom")
    assert passage.whole
    assert passage.text == "Основне.\n\nЗаміна.\n\nХто не платить."


async def test_large_document_gives_hits_with_neighbours(forest: AsyncSession) -> None:
    texts = [f"Частина {i}. " + "слово " * (WHOLE_DOCUMENT_CHARS // 30) for i in range(6)]
    document = await add(forest, "pid-dubom", "Путівник", texts, vector=axis(3))
    # Знайшовся лише фрагмент 3 — у відповідь ідуть 2, 3 і 4
    await forest.execute(
        update(Chunk)
        .where(Chunk.document_id == document.id, Chunk.ord == 3)
        .values(embedding=QUESTION)
    )
    [passage] = await find(forest, "pid-dubom")
    assert not passage.whole
    assert passage.sections == ("Розділ 2", "Розділ 3", "Розділ 4")
    assert passage.pages == (3, 4, 5)
