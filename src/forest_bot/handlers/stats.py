"""Списки й числа для всіх мешканців (S-07): питання без відповіді (D-36), відповіді
«Хибно» (D-50), ліс у числах (D-52). Адмінів немає (R-03), тож бачить кожен — і
кожен може допомогти: надіслати документ чи новий варіант. Імен тут немає."""

from collections.abc import Callable
from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import texts
from forest_bot.config import Settings
from forest_bot.places import PlaceGraph
from forest_bot.qa.render import split_message
from forest_bot.repo import stats

Sessions = async_sessionmaker[AsyncSession]
Clock = Callable[[], datetime]

# Довге питання в списку — початок, решта зрозуміла з контексту
QUESTION_CHARS = 200


def _now() -> datetime:
    return datetime.now(UTC)


def _short(question: str) -> str:
    question = " ".join(question.split())
    return question if len(question) <= QUESTION_CHARS else question[:QUESTION_CHARS] + "…"


async def _answer_long(message: Message, text: str) -> None:
    for chunk in split_message(text):
        await message.answer(chunk)


async def on_unanswered(
    message: Message,
    sessions: Sessions,
    graph: PlaceGraph,
    settings: Settings,
    clock: Clock = _now,
) -> None:
    async with sessions() as session:
        found = await stats.unanswered(session, clock())
    if not found:
        await message.answer(texts.UNANSWERED_EMPTY)
        return
    items = []
    for item in found:
        line = texts.UNANSWERED_ITEM.format(
            date=f"{item.last_asked.astimezone(settings.timezone):%d.%m}",
            question=_short(item.question),
        )
        if item.place_id is not None and item.place_id in graph:
            line += texts.UNANSWERED_PLACE.format(place=graph.get(item.place_id).citation)
        if item.times > 1:
            line += texts.UNANSWERED_TIMES.format(times=item.times)
        items.append(line)
    await _answer_long(message, texts.UNANSWERED.format(items="\n".join(items)))


async def on_wrong(message: Message, sessions: Sessions, settings: Settings) -> None:
    async with sessions() as session:
        found = await stats.wrong_answers(session)
    if not found:
        await message.answer(texts.WRONG_EMPTY)
        return
    items = [
        texts.WRONG_ITEM.format(
            date=f"{item.asked_at.astimezone(settings.timezone):%d.%m}",
            question=_short(item.question),
            sources=(
                texts.WRONG_SOURCES.format(titles=texts.quoted_list(list(item.sources)))
                if item.sources
                else texts.WRONG_NOT_FOUND
            ),
        )
        for item in found
    ]
    await _answer_long(message, texts.WRONG_ANSWERS.format(items="\n\n".join(items)))


async def on_stats(
    message: Message, sessions: Sessions, settings: Settings, clock: Clock = _now
) -> None:
    async with sessions() as session:
        numbers = await stats.numbers(session, clock(), settings.timezone)
    share = f" з {numbers.week}" if numbers.week else ""
    await message.answer(
        texts.STATS.format(
            documents=numbers.archive + numbers.animals,
            archive=numbers.archive,
            animals=numbers.animals,
            members=numbers.members,
            today=numbers.today,
            week=numbers.week,
            unanswered=f"{numbers.unanswered_week}{share}",
            useful=numbers.useful,
            wrong=numbers.wrong,
        )
    )


def build_router() -> Router:
    router = Router(name="stats")
    router.message.register(on_unanswered, Command("unanswered"))
    router.message.register(on_wrong, Command("wrong"))
    router.message.register(on_stats, Command("stats"))
    return router
