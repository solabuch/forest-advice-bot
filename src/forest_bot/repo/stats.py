"""Списки й числа для всіх: питання без відповіді (D-36), «Хибно» (D-50), статистика (D-52).

Без імен: `unanswered` і `qa_log` не зберігають, хто питав.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Chunk, Document, DocumentSource, QaLog, Rating, Unanswered, User

# Давніші питання без відповіді, швидше за все, уже неактуальні
UNANSWERED_DAYS = 30


@dataclass(frozen=True)
class OpenQuestion:
    question: str
    place_id: str | None
    last_asked: datetime
    times: int


@dataclass(frozen=True)
class WrongAnswer:
    question: str
    asked_at: datetime
    found: bool
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Numbers:
    archive: int
    animals: int
    members: int
    today: int
    week: int
    unanswered_week: int
    useful: int
    wrong: int


async def unanswered(session: AsyncSession, now: datetime, limit: int = 20) -> list[OpenQuestion]:
    """Те саме питання про те саме місце — один рядок із кількістю, новіші першими."""
    key = func.lower(func.btrim(Unanswered.question))
    last = func.max(Unanswered.asked_at)
    rows = await session.execute(
        select(func.min(Unanswered.question), Unanswered.place_id, last, func.count())
        .where(Unanswered.asked_at >= now - timedelta(days=UNANSWERED_DAYS))
        .group_by(key, Unanswered.place_id)
        .order_by(last.desc())
        .limit(limit)
    )
    return [OpenQuestion(q, place_id, at, times) for q, place_id, at, times in rows]


async def wrong_answers(session: AsyncSession, limit: int = 15) -> list[WrongAnswer]:
    """Відповіді з «Хибно» — новіші першими, з назвами документів-джерел."""
    logs = list(
        await session.scalars(
            select(QaLog)
            .where(QaLog.rating == Rating.WRONG)
            .order_by(QaLog.rated_at.desc(), QaLog.id.desc())
            .limit(limit)
        )
    )
    chunk_ids = {c for log in logs for c in log.chunk_ids}
    titles: dict[int, str] = {}
    if chunk_ids:
        rows = await session.execute(
            select(Chunk.id, Document.title)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id.in_(chunk_ids))
        )
        titles = dict(rows.all())
    return [
        WrongAnswer(
            log.question,
            log.asked_at,
            log.found,
            # Фрагменти переіндексованого документа зникли — назви просто не буде
            tuple(dict.fromkeys(titles[c] for c in log.chunk_ids if c in titles)),
        )
        for log in logs
    ]


async def _count(session: AsyncSession, *where: ColumnElement[bool]) -> int:
    return int(await session.scalar(select(func.count()).select_from(QaLog).where(*where)) or 0)


async def numbers(session: AsyncSession, now: datetime, timezone: ZoneInfo) -> Numbers:
    """Документи, мешканці, питання за сьогодні (за київським часом) і 7 днів, оцінки."""
    current = select(Document.source, func.count()).where(Document.is_current)
    by_source = dict((await session.execute(current.group_by(Document.source))).all())
    rated = dict(
        (
            await session.execute(
                select(QaLog.rating, func.count())
                .where(QaLog.rating.is_not(None))
                .group_by(QaLog.rating)
            )
        ).all()
    )
    members = await session.scalar(select(func.count()).where(User.member_since.is_not(None)))
    midnight = datetime.combine(now.astimezone(timezone).date(), time(), tzinfo=timezone)
    week_ago = now - timedelta(days=7)
    return Numbers(
        archive=by_source.get(DocumentSource.ARCHIVE, 0),
        animals=by_source.get(DocumentSource.TELEGRAM, 0),
        members=int(members or 0),
        today=await _count(session, QaLog.asked_at >= midnight),
        week=await _count(session, QaLog.asked_at >= week_ago),
        unanswered_week=await _count(session, QaLog.asked_at >= week_ago, QaLog.found.is_(False)),
        useful=rated.get(Rating.USEFUL, 0),
        wrong=rated.get(Rating.WRONG, 0),
    )
