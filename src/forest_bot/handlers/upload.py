"""Telegram: прийом документа, ім'я автора, вибір місця й заміни кнопками (S-05).

Важка робота — у фоні (`ingest/upload.py`); тут лише перевірки, чернетка й відповіді
на кнопки. Стан — у таблиці `uploads`, тож кнопка спрацює й після рестарту.
"""

import logging
from pathlib import PurePath
from typing import Any

from aiogram import F, Router
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot import jobs, keyboards, texts
from forest_bot.config import Settings
from forest_bot.ingest.index import index_document
from forest_bot.ingest.parse import SUPPORTED, ParsedDocument
from forest_bot.ingest.upload import PREPARE, UploadPipeline
from forest_bot.keyboards import UploadAction
from forest_bot.models import Document, UploadStatus
from forest_bot.repo import documents, users
from forest_bot.repo import uploads as upload_repo
from forest_bot.repo.uploads import ACTIVE, IncomingFile
from forest_bot.repo.users import InvalidNameError

logger = logging.getLogger(__name__)

MB = 1024 * 1024


async def on_photo(message: Message) -> None:
    await message.answer(texts.PHOTO_NOT_ACCEPTED)


async def on_document(message: Message, pipeline: UploadPipeline, settings: Settings) -> None:
    document = message.document
    if document is None or message.from_user is None:
        return
    name = document.file_name or "документ"
    if PurePath(name).suffix.lower() not in SUPPORTED:
        await message.answer(texts.UNSUPPORTED)
        return
    if (document.file_size or 0) > settings.max_file_mb * MB:
        await message.answer(texts.TOO_BIG.format(limit=settings.max_file_mb))
        return

    async with pipeline.sessions.begin() as session:
        user = await users.get_or_create(session, message.from_user.id)
        if await upload_repo.recent_count(session, user.id) >= settings.uploads_per_hour:
            await message.answer(texts.TOO_MANY.format(limit=settings.uploads_per_hour))
            return
        waiting = await upload_repo.with_status(session, user.id, UploadStatus.NEEDS_NAME)
        batch = await upload_repo.with_status(session, user.id, UploadStatus.QUEUED)
        upload = await upload_repo.create(
            session,
            user_id=user.id,
            chat_id=message.chat.id,
            batch=message.media_group_id,
            file=IncomingFile(document.file_id, name, document.file_size or 0, document.mime_type),
            status=UploadStatus.QUEUED if user.real_name else UploadStatus.NEEDS_NAME,
        )
        if upload.status is UploadStatus.QUEUED:
            await jobs.enqueue(session, PREPARE, {"upload_id": upload.id})
    pipeline.wake()

    if upload.status is UploadStatus.NEEDS_NAME:
        # Ім'я питаємо раз, навіть якщо файлів кілька (D-34, D-53)
        if not waiting:
            await message.answer(texts.ASK_NAME, reply_markup=keyboards.refuse_name())
    elif message.media_group_id is None:
        await message.answer(texts.RECEIVED.format(name=name))
    elif not any(u.batch == message.media_group_id for u in batch):
        await message.answer(texts.RECEIVED_MANY.format(count="кілька"))


class AwaitingAnswer(Filter):
    """Текст — відповідь на питання бота (ім'я чи нова назва), а не питання про пораду."""

    async def __call__(self, message: Message, pipeline: UploadPipeline) -> bool | dict[str, Any]:
        if message.from_user is None or not message.text or message.text.startswith("/"):
            return False
        async with pipeline.sessions() as session:
            user_id = message.from_user.id
            if await upload_repo.with_status(session, user_id, UploadStatus.NEEDS_NAME):
                return {"awaiting": "name"}
            if titles := await upload_repo.with_status(session, user_id, UploadStatus.NEEDS_TITLE):
                return {"awaiting": "title", "upload_id": titles[-1].id}
        return False


async def on_answer(
    message: Message, pipeline: UploadPipeline, awaiting: str, upload_id: int = 0
) -> None:
    if message.from_user is None or message.text is None:
        return
    if awaiting == "name":
        await _set_name(message, pipeline, message.from_user.id, message.text)
    else:
        await _set_title(message, pipeline, message.from_user.id, message.text, upload_id)


async def _set_name(message: Message, pipeline: UploadPipeline, user_id: int, text: str) -> None:
    async with pipeline.sessions.begin() as session:
        user = await users.get_or_create(session, user_id)
        try:
            name = await users.set_name(session, user, text)
        except InvalidNameError as error:
            # Зайняте ім'я — попросимо розрізнювальне слово (D-44)
            await message.answer(str(error))
            return
        await release_waiting(session, user.id)
    pipeline.wake()
    await message.answer(texts.NAME_SAVED.format(name=name))


async def release_waiting(session: AsyncSession, user_id: int) -> int:
    """Звір назвався — документи, що чекали на ім'я, ідуть у розбір (D-34)."""
    waiting = await upload_repo.with_status(session, user_id, UploadStatus.NEEDS_NAME)
    for upload in waiting:
        upload.status = UploadStatus.QUEUED
        await jobs.enqueue(session, PREPARE, {"upload_id": upload.id})
    return len(waiting)


async def _set_title(
    message: Message, pipeline: UploadPipeline, user_id: int, text: str, upload_id: int
) -> None:
    title = " ".join(text.split())[:300]
    async with pipeline.sessions.begin() as session:
        upload = await upload_repo.get_own(session, upload_id, user_id)
        if upload is None or upload.document_id is None or not title:
            return
        upload.status, upload.title = UploadStatus.DONE, title
        document = await session.get_one(Document, upload.document_id, with_for_update=True)
        document.title, document.title_key = title, documents.title_key(title)
        await _reindex(pipeline, session, document, upload.parsed)
    await message.answer(texts.TITLE_SAVED.format(title=title))


async def _reindex(
    pipeline: UploadPipeline,
    session: AsyncSession,
    document: Document,
    parsed: dict[str, Any] | None,
) -> None:
    """Назва й місце — у заголовку кожного фрагмента: після зміни векторизуємо заново."""
    if parsed is None or not document.is_current:
        return
    await index_document(
        session,
        document,
        ParsedDocument.from_json(parsed),
        pipeline.embedder,
        pipeline.graph,
        pipeline.translator,
    )


async def on_button(
    query: CallbackQuery, callback_data: UploadAction, pipeline: UploadPipeline
) -> None:
    user_id = query.from_user.id
    reply = query.message.answer if isinstance(query.message, Message) else None
    # Кнопки під повідомленням більше не потрібні: подвійне натискання нічого не зламає
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
    await query.answer()

    action, value = callback_data.action, callback_data.value
    async with pipeline.sessions.begin() as session:
        if action == "refuse_name":
            for waiting in await upload_repo.with_status(session, user_id, UploadStatus.NEEDS_NAME):
                waiting.status = UploadStatus.CANCELLED
            if reply:
                await reply(texts.NAME_REFUSED)
            return

        upload = await upload_repo.get_own(session, callback_data.id, user_id)
        if upload is None:
            return
        if action == "cancel" and upload.status in ACTIVE:
            upload.status = UploadStatus.CANCELLED
            if reply:
                await reply(texts.CANCELLED.format(title=upload.title or upload.file_name))
        elif action == "place" and upload.status is UploadStatus.NEEDS_PLACE:
            upload.place_id = value if value in pipeline.graph else None
            if upload.place_id:
                await pipeline.after_place(session, upload)
        elif action == "replace" and upload.status is UploadStatus.NEEDS_CHOICE:
            if value.isdigit() and int(value) in upload.candidate_ids:
                upload.replaces_id = int(value)
                await pipeline.start_index(session, upload)
        elif action == "separate" and upload.status is UploadStatus.NEEDS_CHOICE:
            await pipeline.start_index(session, upload)
        elif action == "title" and upload.status is UploadStatus.DONE:
            upload.status = UploadStatus.NEEDS_TITLE
            if reply:
                await reply(texts.ASK_TITLE.format(title=upload.title))
        elif action == "move" and upload.status is UploadStatus.DONE and reply:
            await reply(
                texts.ASK_MOVE.format(title=upload.title),
                reply_markup=keyboards.place_picker(upload.id, pipeline.graph, action="moveto"),
            )
        elif action == "moveto" and upload.document_id and value in pipeline.graph:
            document = await session.get_one(Document, upload.document_id, with_for_update=True)
            document.place_id = upload.place_id = value
            await session.flush()
            await _reindex(pipeline, session, document, upload.parsed)
            if reply:
                await reply(
                    texts.PLACE_SAVED.format(title=document.title, place=pipeline.place_name(value))
                )
    pipeline.wake()


def build_router() -> Router:
    """Новий router для кожного диспетчера: aiogram не дає ділити один між кількома."""
    router = Router(name="upload")
    router.message.register(on_photo, F.photo)
    router.message.register(on_document, F.document)
    router.message.register(on_answer, F.text, AwaitingAnswer())
    router.callback_query.register(on_button, UploadAction.filter())
    return router
