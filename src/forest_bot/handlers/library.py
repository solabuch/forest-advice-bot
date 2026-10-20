"""Документи й місця (S-07): «Мої документи» з видаленням (D-05, D-43), «Документи місця»
(D-25), список місць.

Кнопки несуть id документа чи місця, тож працюють і після рестарту. Чий документ —
перевіряє репозиторій, а не кнопка: чужий id у callback нічого не видалить.
"""

import logging
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import keyboards, texts
from forest_bot.config import Settings
from forest_bot.keyboards import LibraryAction
from forest_bot.models import Document, DocumentStatus
from forest_bot.places import PlaceGraph, PlaceKind
from forest_bot.qa.render import source_of, split_message
from forest_bot.repo import documents, users
from forest_bot.repo.documents import DocumentError

logger = logging.getLogger(__name__)

Sessions = async_sessionmaker[AsyncSession]

# Під списком — кнопки лише для найновіших: довша клавіатура незручна в Telegram
MAX_DELETE_BUTTONS = 20


def _citation(graph: PlaceGraph, place_id: str) -> str:
    # Місце прибрали з places.yaml, а документ лишився — показуємо хоч id
    return graph.get(place_id).citation if place_id in graph else place_id


def _status(document: Document) -> str:
    if document.is_current:
        return texts.STATUS_CURRENT
    if document.status is DocumentStatus.PROCESSING:
        return texts.STATUS_PROCESSING
    if document.status is DocumentStatus.FAILED:
        return texts.STATUS_FAILED
    return texts.STATUS_REPLACED


async def _answer_long(
    message: Message, text: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Довгий список — кількома повідомленнями; кнопки — під останнім."""
    chunks = split_message(text)
    for chunk in chunks[:-1]:
        await message.answer(chunk)
    await message.answer(chunks[-1], reply_markup=markup)


async def on_my(message: Message, sessions: Sessions, graph: PlaceGraph) -> None:
    """«Мої документи» (D-05): усі свої, з місцем і станом; видалення — кнопкою."""
    if message.from_user is None:
        return
    async with sessions() as session:
        own = await documents.of_user(session, message.from_user.id)
    if not own:
        await message.answer(texts.MY_EMPTY)
        return
    items = [
        texts.MY_ITEM.format(title=d.title, place=_citation(graph, d.place_id), status=_status(d))
        for d in own
    ]
    # Документ, що ще обробляється, не видаляємо: індексація зробила б його чинним
    deletable = [(d.id, d.title) for d in own if d.status is not DocumentStatus.PROCESSING]
    text = texts.MY_DOCUMENTS.format(items="\n".join(items))
    if deletable:
        text += f"\n\n{texts.MY_DELETE_HINT}"
    markup = keyboards.my_documents(deletable[:MAX_DELETE_BUTTONS]) if deletable else None
    await _answer_long(message, text, markup)


async def on_docs(message: Message, graph: PlaceGraph) -> None:
    await message.answer(texts.DOCS_ASK_PLACE, reply_markup=keyboards.library_places(graph))


async def on_places(message: Message, graph: PlaceGraph) -> None:
    """Місця лісу з частинами: «Під вербою» — в обох своїх частинах (D-21)."""
    mittens = graph.of_kind(PlaceKind.MITTEN)

    def names(found: list[str]) -> str:
        return ", ".join(f"«{name}»" for name in found)

    lines = [
        texts.PLACES_PART.format(
            part=part.name, mittens=names([m.name for m in mittens if part.id in m.parts])
        )
        for part in graph.of_kind(PlaceKind.PART)
    ]
    if outside := [m.name for m in mittens if not m.parts]:
        lines.append(texts.PLACES_OUTSIDE.format(mittens=names(outside)))
    await message.answer(texts.PLACES.format(forest=graph.forest.name, parts="\n".join(lines)))


async def place_documents(
    sessions: Sessions, graph: PlaceGraph, timezone: ZoneInfo, place_id: str
) -> str:
    """Чинні документи місця, його частин і лісу — групами від конкретнішого (D-25)."""
    async with sessions() as session:
        found = await documents.current_for_place(session, graph, place_id)
        names = await users.names(session, (d.sender_id for d in found))
    place = graph.get(place_id)
    if not found:
        return texts.PLACE_EMPTY.format(place=place.citation)
    applicable = graph.applicable(place_id)
    groups = []
    for item in applicable:
        lines = [
            texts.PLACE_ITEM.format(title=d.title, source=source_of(d, names, timezone))
            for d in found
            if d.place_id == item.place.id
        ]
        if not lines:
            continue
        if len(applicable) > 1:
            lines.insert(0, texts.PLACE_GROUP.format(place=item.place.citation))
        groups.append("\n".join(lines))
    return texts.PLACE_DOCUMENTS.format(place=place.citation) + "\n\n" + "\n\n".join(groups)


async def _own(session: AsyncSession, document_id: int, user_id: int) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise DocumentError(texts.DOCUMENT_GONE)
    if document.sender_id != user_id:
        raise DocumentError(texts.NOT_YOUR_DOCUMENT)
    return document


async def _delete(sessions: Sessions, document_id: int, user_id: int, timezone: ZoneInfo) -> str:
    """Видаляє свій документ; був чинним — знову діє попередній варіант (D-43)."""
    async with sessions.begin() as session:
        title = (await _own(session, document_id, user_id)).title
        previous = await documents.delete(session, document_id, user_id)
        if previous is None:
            logger.info("Документ #%s видалено", document_id)
            return texts.DELETED.format(title=title)
        names = await users.names(session, [previous.sender_id])
        logger.info("Документ #%s видалено, знову чинний #%s", document_id, previous.id)
        return texts.DELETED_RESTORED.format(
            title=title, previous=previous.title, source=source_of(previous, names, timezone)
        )


async def on_button(
    query: CallbackQuery,
    callback_data: LibraryAction,
    sessions: Sessions,
    graph: PlaceGraph,
    settings: Settings,
) -> None:
    message = query.message if isinstance(query.message, Message) else None
    action, user_id = callback_data.action, query.from_user.id
    if message is None:
        await query.answer()
        return
    try:
        if action == "place" and callback_data.value in graph:
            reply = await place_documents(sessions, graph, settings.timezone, callback_data.value)
            await query.answer()
            await _answer_long(message, reply)
        elif action == "delete":
            async with sessions() as session:
                document = await _own(session, callback_data.id, user_id)
            await query.answer()
            await message.answer(
                texts.ASK_DELETE.format(title=document.title),
                reply_markup=keyboards.confirm_delete(document.id),
            )
        elif action in ("delete_yes", "delete_no"):
            # Підтвердження — один раз: подвійне натискання нічого не зламає
            await message.edit_reply_markup(reply_markup=None)
            if action == "delete_yes":
                reply = await _delete(sessions, callback_data.id, user_id, settings.timezone)
            else:
                async with sessions() as session:
                    reply = texts.KEPT.format(
                        title=(await _own(session, callback_data.id, user_id)).title
                    )
            await query.answer()
            await message.answer(reply)
        else:
            await query.answer()
    except DocumentError as error:
        await query.answer(str(error), show_alert=True)


def build_router() -> Router:
    router = Router(name="library")
    router.message.register(on_my, Command("my"))
    router.message.register(on_docs, Command("docs"))
    router.message.register(on_places, Command("places"))
    router.callback_query.register(on_button, LibraryAction.filter())
    return router
