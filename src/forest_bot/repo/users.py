"""Звірі: членство (D-54) і справжні імена (R-06, D-29, D-34, D-44)."""

import re
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import JoinedVia, User

NAME_MIN, NAME_MAX = 2, 60
# Літери будь-якої абетки, пробіл, дефіс, апостроф: «Зайчик Сірий», «Мишка-шкряботушка»
NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '’ʼ-][^\W\d_]+)*$")


class InvalidNameError(Exception):
    """Ім'я не прийнято; повідомлення — для звіра."""


class NameTakenError(InvalidNameError):
    """Ім'я вже має інший звір (D-44): треба розрізнювальне слово."""


def clean_name(raw: str) -> str:
    """Прибирає зайві пробіли; невалідне ім'я → InvalidNameError з поясненням."""
    name = re.sub(r"\s+", " ", raw).strip()
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise InvalidNameError(f"Ім'я має бути від {NAME_MIN} до {NAME_MAX} символів")
    if not NAME_RE.match(name):
        raise InvalidNameError("Ім'я — лише з літер, можна з пробілом, дефісом чи апострофом")
    return name


def name_key(name: str) -> str:
    return name.casefold().replace("’", "'").replace("ʼ", "'")


async def get_or_create(session: AsyncSession, user_id: int) -> User:
    await session.execute(insert(User).values(id=user_id).on_conflict_do_nothing())
    return await session.get_one(User, user_id)


async def has_members(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(exists().where(User.member_since.is_not(None)))))


async def admit(session: AsyncSession, user_id: int, via: JoinedVia) -> User:
    """Робить звіра учасником; повторний вхід нічого не змінює."""
    user = await get_or_create(session, user_id)
    if user.member_since is None:
        user.member_since = datetime.now(UTC)
        user.joined_via = via
    return user


async def set_name(session: AsyncSession, user: User, raw: str) -> str:
    """Задає чи змінює справжнє ім'я (D-29); зайняте іншим звіром → NameTakenError (D-44)."""
    name = clean_name(raw)
    key = name_key(name)
    owner = await session.scalar(select(User.id).where(User.name_key == key))
    if owner is not None and owner != user.id:
        raise NameTakenError(f"Ім'я «{name}» уже має інший звір — додай слово, що тебе розрізнить")
    user.real_name, user.name_key = name, key
    await session.flush()
    return name
