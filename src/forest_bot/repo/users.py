"""Звірі: членство (D-54), справжні імена (R-06, D-29, D-34, D-44) і «Забути мене» (D-51)."""

import re
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Chunk, JoinedVia, Subscription, Upload, UploadStatus, User
from forest_bot.repo import documents
from forest_bot.repo.uploads import ACTIVE

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


async def names(session: AsyncSession, user_ids: Iterable[int | None]) -> dict[int, str]:
    """Справжні імена авторів — для підпису «надіслано: Ім'я, дата» (R-12)."""
    ids = {i for i in user_ids if i is not None}
    if not ids:
        return {}
    rows = await session.execute(
        select(User.id, User.real_name).where(User.id.in_(ids), User.real_name.is_not(None))
    )
    return {user_id: name for user_id, name in rows if name}


async def has_members(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(exists().where(User.member_since.is_not(None)))))


async def admit(session: AsyncSession, user_id: int, via: JoinedVia) -> User:
    """Робить звіра учасником; повторний вхід нічого не змінює."""
    user = await get_or_create(session, user_id)
    if user.member_since is None:
        user.member_since = datetime.now(UTC)
        user.joined_via = via
    return user


async def check_name(session: AsyncSession, user_id: int, raw: str) -> str:
    """Чисте ім'я, якщо його можна взяти; зайняте іншим звіром → NameTakenError (D-44)."""
    name = clean_name(raw)
    owner = await session.scalar(select(User.id).where(User.name_key == name_key(name)))
    if owner is not None and owner != user_id:
        raise NameTakenError(f"Ім'я «{name}» уже має інший звір — додай слово, що тебе розрізнить")
    return name


async def set_name(session: AsyncSession, user: User, raw: str) -> str:
    """Задає чи змінює справжнє ім'я (D-29): нове одразу видно в підписах усіх документів."""
    name = await check_name(session, user.id, raw)
    user.real_name, user.name_key = name, name_key(name)
    await session.flush()
    return name


async def forget(session: AsyncSession, user_id: int) -> int:
    """«Забути мене» (D-51): ім'я, підписки, документи; замінені ними варіанти знову діють.

    Членство лишається — питати можна й далі. Рядки документів лишаються в історії (D-13),
    бо на них посилаються інші версії лінії, але текст і фрагменти зникають. Повертає,
    скільки документів видалено.
    """
    user = await session.get_one(User, user_id, with_for_update=True)
    # Чернетки в роботі — скасувати. Індексація перевіряє стан під блокуванням чернетки,
    # тож документ, що саме індексується, або встигне (і видалиться нижче), або ні
    await session.execute(
        update(Upload)
        .where(Upload.user_id == user_id, Upload.status.in_(ACTIVE))
        .values(status=UploadStatus.CANCELLED)
    )
    await session.execute(update(Upload).where(Upload.user_id == user_id).values(parsed=None))
    own = await documents.of_user(session, user_id)
    # Новіші першими: кожне видалення повертає попередній варіант (D-43)
    for document in own:
        await documents.delete(session, document.id, user_id)
    if own:
        await session.execute(delete(Chunk).where(Chunk.document_id.in_([d.id for d in own])))
    await session.execute(delete(Subscription).where(Subscription.user_id == user_id))
    user.real_name = user.name_key = None
    await session.flush()
    return len(own)
