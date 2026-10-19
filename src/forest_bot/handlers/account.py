"""Ім'я й «Забути мене» (S-07): зміна імені з підтвердженням (D-29, D-44), «Забути мене»
з підтвердженням (D-51).

Стану в пам'яті немає: нове ім'я бот просить відповіддю на своє повідомлення (ForceReply),
а кнопка підтвердження стоїть під відповіддю звіра, тож ім'я береться з `reply_to_message`.
Так діалог переживає рестарт.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, ForceReply, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import keyboards, texts
from forest_bot.handlers.upload import release_waiting
from forest_bot.ingest.upload import UploadPipeline
from forest_bot.keyboards import AccountAction
from forest_bot.models import User
from forest_bot.repo import documents, users
from forest_bot.repo.users import InvalidNameError

logger = logging.getLogger(__name__)

Sessions = async_sessionmaker[AsyncSession]

NAME_REPLY = ForceReply(force_reply=True, input_field_placeholder=texts.NEW_NAME_PLACEHOLDER)


async def on_rename(message: Message, sessions: Sessions) -> None:
    if message.from_user is None:
        return
    async with sessions() as session:
        user = await session.get(User, message.from_user.id)
    name = user.real_name if user else None
    text = texts.ASK_NEW_NAME.format(name=name) if name else texts.ASK_FIRST_NAME
    await message.answer(text, reply_markup=NAME_REPLY)


class NewName(Filter):
    """Відповідь на прохання бота написати нове ім'я."""

    async def __call__(self, message: Message) -> bool:
        asked = message.reply_to_message
        return bool(
            message.text
            and not message.text.startswith("/")
            and asked is not None
            and asked.from_user is not None
            and asked.from_user.is_bot
            and asked.text
            and texts.RENAME_PROMPT in asked.text
        )


async def on_new_name(message: Message, sessions: Sessions) -> None:
    if message.from_user is None or message.text is None:
        return
    try:
        async with sessions() as session:
            name = await users.check_name(session, message.from_user.id, message.text)
    except InvalidNameError as error:
        await message.answer(texts.RENAME_RETRY.format(error=error), reply_markup=NAME_REPLY)
        return
    await message.reply(
        texts.CONFIRM_RENAME.format(name=name),
        reply_markup=keyboards.confirm(texts.RENAME_YES, texts.RENAME_NO, "rename"),
    )


async def on_forget(message: Message, sessions: Sessions) -> None:
    if message.from_user is None:
        return
    async with sessions() as session:
        count = len(await documents.of_user(session, message.from_user.id))
    await message.answer(
        texts.FORGET_ASK.format(count=count),
        reply_markup=keyboards.confirm(texts.FORGET_YES, texts.FORGET_NO, "forget"),
    )


async def _rename(
    message: Message, sessions: Sessions, pipeline: UploadPipeline, user_id: int
) -> None:
    typed = message.reply_to_message.text if message.reply_to_message else None
    if not typed:
        await message.answer(texts.RENAME_LOST)
        return
    try:
        async with sessions.begin() as session:
            user = await users.get_or_create(session, user_id)
            name = await users.set_name(session, user, typed)
            # Документи, що чекали на ім'я, тепер можна розбирати
            released = await release_waiting(session, user_id)
    except InvalidNameError as error:
        await message.answer(texts.RENAME_RETRY.format(error=error), reply_markup=NAME_REPLY)
        return
    if released:
        pipeline.wake()
    logger.info("Звір змінив ім'я")
    await message.answer(texts.RENAMED.format(name=name))


async def on_button(
    query: CallbackQuery,
    callback_data: AccountAction,
    sessions: Sessions,
    pipeline: UploadPipeline,
) -> None:
    message = query.message if isinstance(query.message, Message) else None
    if message is None:
        await query.answer()
        return
    # Підтвердження — один раз: подвійне натискання нічого не зламає
    await message.edit_reply_markup(reply_markup=None)
    await query.answer()
    action = callback_data.action
    if action == "rename_yes":
        await _rename(message, sessions, pipeline, query.from_user.id)
    elif action == "rename_no":
        await message.answer(texts.RENAME_KEPT)
    elif action == "forget_yes":
        async with sessions.begin() as session:
            count = await users.forget(session, query.from_user.id)
        logger.info("Звіра забуто, документів видалено: %d", count)
        await message.answer(texts.FORGOTTEN.format(count=count))
    elif action == "forget_no":
        await message.answer(texts.FORGET_KEPT)


def build_router() -> Router:
    """Перед роутером завантажень: відповідь із новим ім'ям — не ім'я для документа й не
    питання."""
    router = Router(name="account")
    router.message.register(on_rename, Command("rename"))
    router.message.register(on_forget, Command("forget"))
    router.message.register(on_new_name, F.text, NewName())
    router.callback_query.register(on_button, AccountAction.filter())
    return router
