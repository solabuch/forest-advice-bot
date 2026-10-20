"""Доступ за запрошенням (D-54, ADR-17) і лише особисті чати (R-25).

Стежки до рукавичок і графіки чергувань — не для чужих («в лісі бувають мисливці»).
Адмінів немає (R-03): запрошує будь-який учасник, а перший, хто напише свіжому боту,
стає першим мешканцем.
"""

import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject
from aiogram.types import User as TelegramUser
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import texts
from forest_bot.config import Settings
from forest_bot.models import BotSetting, JoinedVia, User
from forest_bot.repo import users

logger = logging.getLogger(__name__)

INVITE_SETTING = "invite_code"
# Перший учасник — рівно один, навіть якщо двоє напишуть свіжому боту одночасно
_FIRST_MEMBER_LOCK = 540_001

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


async def invite_code(session: AsyncSession, settings: Settings) -> str:
    """Код із налаштувань (його й міняють, коли посилання злили); немає — свій, у БД."""
    if settings.invite_code is not None:
        return settings.invite_code.get_secret_value()
    await session.execute(
        insert(BotSetting)
        .values(key=INVITE_SETTING, value=secrets.token_urlsafe(12))
        .on_conflict_do_nothing()
    )
    code = await session.scalar(select(BotSetting.value).where(BotSetting.key == INVITE_SETTING))
    if code is None:
        raise RuntimeError("код запрошення не збережено")
    return code


def invite_link(username: str, code: str) -> str:
    return f"https://t.me/{username}?start={code}"


def _start_code(event: TelegramObject) -> str | None:
    """`/start <код>` з посилання-запрошення."""
    if isinstance(event, Message) and event.text and event.text.startswith("/start"):
        parts = event.text.split(maxsplit=1)
        return parts[1].strip() if len(parts) == 2 else ""
    return None


class Access(BaseMiddleware):
    """Перед усіма хендлерами повідомлень і кнопок: лише учасники й лише особисті чати."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        chat = data.get("event_chat")
        user: TelegramUser | None = data.get("event_from_user")
        if chat is not None and chat.type != ChatType.PRIVATE:
            return None  # R-25: у групах бот мовчить (і виходить — див. on_membership)
        if user is None or user.is_bot:
            return None
        refusal = await self._admit(user.id, event)
        if refusal is None:
            return await handler(event, data)
        if isinstance(event, Message):
            await event.answer(refusal)
        elif isinstance(event, CallbackQuery):
            await event.answer(refusal, show_alert=True)
        return None

    async def _admit(self, user_id: int, event: TelegramObject) -> str | None:
        """None — пропустити; інакше — що сказати чужому."""
        async with self.sessions.begin() as session:
            member = await session.scalar(
                select(User.member_since.is_not(None)).where(User.id == user_id)
            )
            if member:
                return None
            code = _start_code(event)
            if code:
                # Байти: код можуть набрати вручну кирилицею, а рядки compare_digest — лише ASCII
                expected = await invite_code(session, self.settings)
                if secrets.compare_digest(code.encode(), expected.encode()):
                    await users.admit(session, user_id, JoinedVia.INVITE)
                    logger.info("Новий мешканець за запрошенням")
                    return None
                return texts.INVITE_EXPIRED
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _FIRST_MEMBER_LOCK}
            )
            if not await users.has_members(session):
                await users.admit(session, user_id, JoinedVia.FIRST)
                logger.info("Перший мешканець свіжого бота")
                return None
        return texts.NOT_MEMBER


async def on_invite(
    message: Message,
    bot: Bot,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """«Запросити сусіда»: посилання можна переслати — тому без protect_content."""
    async with sessions.begin() as session:
        code = await invite_code(session, settings)
    me = await bot.me()
    await message.answer(
        texts.INVITE.format(link=invite_link(me.username or "", code)), protect_content=False
    )


async def on_membership(event: ChatMemberUpdated, bot: Bot) -> None:
    """Бота додали в групу чи канал — чемно виходить (R-25)."""
    if event.chat.type != ChatType.PRIVATE and event.new_chat_member.status in (
        "member",
        "administrator",
    ):
        logger.info("Бота додали в групу — виходжу")
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(event.chat.id, texts.PRIVATE_ONLY)
        await bot.leave_chat(event.chat.id)
