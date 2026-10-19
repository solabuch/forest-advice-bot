"""Списки й числа для всіх через Dispatcher: питання без відповіді (D-36), «Хибно» (D-50),
ліс у числах (D-52). Імен тут немає — у цих таблицях їх і не зберігаємо."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from forest_bot import texts
from forest_bot.models import Chunk, QaLog, Rating, Unanswered
from forest_bot.repo.places import sync_places
from tests.integration.test_search import add
from tests.integration.test_upload_flow import GRAPH, Forest, Sessions, ready_fees

pytestmark = pytest.mark.integration

# 15:00 за Києвом
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
async def forest(sessions: Sessions) -> Forest:
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
    forest = Forest(sessions)
    forest.dp.workflow_data["clock"] = lambda: NOW
    return forest


def log(
    question: str,
    ago: timedelta,
    *,
    found: bool = True,
    rating: Rating | None = None,
    chunk_ids: list[int] | None = None,
) -> QaLog:
    return QaLog(
        question=question,
        place_ids=["pid-dubom"],
        chunk_ids=chunk_ids or [],
        answer="…",
        found=found,
        asked_at=NOW - ago,
        rating=rating,
        rated_at=NOW - ago if rating else None,
    )


async def test_unanswered_groups_repeats_and_drops_old(forest: Forest) -> None:
    async with forest.sessions.begin() as session:
        session.add_all(
            [
                Unanswered(question="Де взяти мед?", place_id="pid-dubom", asked_at=NOW),
                Unanswered(
                    question="Де взяти мед? ",
                    place_id="pid-dubom",
                    asked_at=NOW - timedelta(hours=2),
                ),
                Unanswered(question="Коли прилітають лелеки?", asked_at=NOW - timedelta(days=1)),
                Unanswered(question="Старе питання?", asked_at=NOW - timedelta(days=40)),
            ]
        )
    await forest.say("/unanswered")
    assert forest.replies()[-1] == texts.UNANSWERED.format(
        items="• 19.10 — «Де взяти мед?» — рукавички «Під дубом» (×2)\n"
        "• 18.10 — «Коли прилітають лелеки?»"
    )


async def test_unanswered_empty(forest: Forest) -> None:
    await forest.say("/unanswered")
    assert forest.replies()[-1] == texts.UNANSWERED_EMPTY


async def test_wrong_answers_with_sources(forest: Forest) -> None:
    async with forest.sessions.begin() as session:
        document = await add(session, "pid-dubom", "Членські внески", ["Внесок — 4 жолуді."])
        chunk_ids = list(
            await session.scalars(select(Chunk.id).where(Chunk.document_id == document.id))
        )
        session.add_all(
            [
                log(
                    "Скільки внесок?",
                    timedelta(hours=1),
                    rating=Rating.WRONG,
                    chunk_ids=chunk_ids,
                ),
                log("Де брід?", timedelta(days=2), found=False, rating=Rating.WRONG),
                log("Що їсти?", timedelta(hours=3), rating=Rating.USEFUL),
            ]
        )
    await forest.say("/wrong")
    assert forest.replies()[-1] == texts.WRONG_ANSWERS.format(
        items="• 19.10 — «Скільки внесок?»\n  джерела: «Членські внески»\n\n"
        "• 17.10 — «Де брід?»\n  бот не знайшов поради"
    )


async def test_wrong_answers_empty(forest: Forest) -> None:
    await forest.say("/wrong")
    assert forest.replies()[-1] == texts.WRONG_EMPTY


async def test_stats(forest: Forest) -> None:
    await ready_fees(forest)
    async with forest.sessions.begin() as session:
        await add(session, "forest", "Пожежа")
        old = await add(session, "forest", "Брід 2025")
        await add(session, "forest", "Брід 2026", lineage=old)
        session.add_all(
            [
                log("Сьогодні 1", timedelta(hours=1), rating=Rating.USEFUL),
                log("Сьогодні 2", timedelta(hours=2), found=False),
                log("Цього тижня", timedelta(days=3), found=False, rating=Rating.WRONG),
                log("Давно", timedelta(days=10), rating=Rating.USEFUL),
            ]
        )
    await forest.say("/stats")
    assert forest.replies()[-1] == texts.STATS.format(
        documents=3,
        archive=2,
        animals=1,
        members=1,
        today=2,
        week=3,
        unanswered="2 з 3",
        useful=2,
        wrong=1,
    )
