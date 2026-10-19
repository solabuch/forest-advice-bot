"""Питання в Telegram (S-06): текст → відповідь із джерелами й кнопками.

Місце не назване — кнопки (D-06). Кнопки місця приходять відповіддю на повідомлення
з питанням, тож питання береться з `reply_to_message`: вибір працює й після рестарту.
Уточнення без місця протягом 30 хвилин — місце з попереднього питання (D-24).
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.chat_action import ChatActionSender
from sqlalchemy import update

from forest_bot import keyboards, texts
from forest_bot.config import Settings
from forest_bot.keyboards import AskAction
from forest_bot.llm.client import LlmError, LlmUnavailableError
from forest_bot.models import Document, QaLog, Rating
from forest_bot.qa.answer import Answerer, Reply
from forest_bot.qa.places import find_places
from forest_bot.qa.render import split_message

logger = logging.getLogger(__name__)

CONTEXT_TTL = 30 * 60
# Довгий текст без питання — це порада, яку варто надіслати файлом (A-07)
ADVICE_MIN_CHARS = 280
_GREETING = re.compile(
    r"^(привіт\w*|вітаю|добр(ий|ого)\s+(день|ранку|вечора|дня)|доброго|хай|hello|hi)\b", re.I
)
_THANKS = re.compile(r"^(дякую|дяки|дяк|спасибі|щиро дякую)\b", re.I)


@dataclass(frozen=True)
class _Context:
    question: str
    place_ids: tuple[str, ...]
    at: float


class DialogMemory:
    """Попереднє питання звіра на 30 хвилин (D-24). У пам'яті процесу: після рестарту
    бот просто перепитає місце кнопками — нічого не губиться."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._items: dict[int, _Context] = {}
        self._clock = clock

    def remember(self, user_id: int, question: str, place_ids: tuple[str, ...]) -> None:
        self._items[user_id] = _Context(question[-400:], place_ids, self._clock())

    def recall(self, user_id: int) -> _Context | None:
        context = self._items.get(user_id)
        if context and self._clock() - context.at <= CONTEXT_TTL:
            return context
        self._items.pop(user_id, None)
        return None


def small_talk(text: str) -> str | None:
    """Привітання й подяка — шаблоном, без LLM (D-31)."""
    if len(text.split()) > 4:
        return None
    if _THANKS.match(text):
        return texts.THANKS
    if _GREETING.match(text):
        return texts.GREETING
    return None


async def on_text(message: Message, answerer: Answerer, memory: DialogMemory, bot: Bot) -> None:
    if message.text is None or message.from_user is None:
        return
    text = message.text.strip()
    if reply := small_talk(text):
        await message.answer(reply)
        return
    if len(text) >= ADVICE_MIN_CHARS and "?" not in text:
        await message.answer(texts.ADVICE_AS_TEXT)
        return
    context = None
    if not find_places(text, answerer.graph).place_ids:
        context = memory.recall(message.from_user.id)
    await _answer(message, text, answerer, memory, bot, context=context)


async def _answer(
    question_message: Message,
    question: str,
    answerer: Answerer,
    memory: DialogMemory,
    bot: Bot,
    *,
    place_ids: tuple[str, ...] | None = None,
    context: _Context | None = None,
    user_id: int | None = None,
) -> None:
    """Відповідь на `question_message`: «друкує…», попередження про затримку, кнопки."""
    chat_id = question_message.chat.id
    user_id = user_id or (question_message.from_user.id if question_message.from_user else 0)
    warned = False

    async def warn(seconds: float) -> None:
        nonlocal warned
        if not warned:
            warned = True
            await bot.send_message(chat_id, texts.WAITING.format(seconds=round(seconds)))

    if context is not None:
        place_ids = context.place_ids
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
            reply = await answerer.ask(
                question,
                place_ids,
                previous=context.question if context else "",
                on_wait=warn,
            )
    except LlmUnavailableError as error:
        await question_message.reply(texts.BUSY.format(seconds=round(error.retry_in or 60)))
        return
    except LlmError:
        logger.exception("Відповідь не вдалася")
        await question_message.reply(texts.LLM_FAILED)
        return

    if reply.needs_place:
        await question_message.reply(
            texts.ASK_QUESTION_PLACE, reply_markup=keyboards.question_places(answerer.graph)
        )
        return
    text = reply.text
    if context is not None:
        names = " і ".join(answerer.graph.get(p).citation for p in reply.place_ids)
        text = f"{texts.FROM_CONTEXT.format(place=names)}\n\n{text}"
    await _send(question_message, text, reply, other_place=context is not None)
    remembered = f"{context.question} {question}" if context else question
    memory.remember(user_id, remembered, reply.place_ids)


async def _send(question_message: Message, text: str, reply: Reply, *, other_place: bool) -> None:
    chunks = split_message(text)
    for chunk in chunks[:-1]:
        await question_message.reply(chunk)
    markup = keyboards.answer_actions(
        [(s.document_id, s.title) for s in reply.sources],
        reply.qa_id,
        other_place=other_place,
    )
    await question_message.reply(chunks[-1], reply_markup=markup)


def _question_of(query: CallbackQuery) -> Message | None:
    """Повідомлення звіра з питанням: бот відповідав на нього, тож воно в reply_to_message."""
    if isinstance(query.message, Message) and query.message.reply_to_message:
        original = query.message.reply_to_message
        return original if original.text else None
    return None


async def on_button(
    query: CallbackQuery,
    callback_data: AskAction,
    answerer: Answerer,
    memory: DialogMemory,
    settings: Settings,
    bot: Bot,
) -> None:
    action, value = callback_data.action, callback_data.value
    if action == "rate":
        await _rate(query, answerer, callback_data.id, value)
        return
    if action == "doc":
        await query.answer()
        await _send_document(query, answerer, settings, bot, callback_data.id)
        return
    original = _question_of(query)
    if original is None or original.text is None:
        await query.answer(texts.QUESTION_LOST, show_alert=True)
        return
    await query.answer()
    if action == "other":
        await original.reply(
            texts.ASK_QUESTION_PLACE, reply_markup=keyboards.question_places(answerer.graph)
        )
    elif action == "place" and value in answerer.graph:
        if isinstance(query.message, Message):
            await query.message.edit_reply_markup(reply_markup=None)
        await _answer(
            original,
            original.text,
            answerer,
            memory,
            bot,
            place_ids=(value,),
            user_id=query.from_user.id,
        )


async def _rate(query: CallbackQuery, answerer: Answerer, qa_id: int, value: str) -> None:
    """«Корисно» / «Хибно» → qa_log (D-50); «Хибно» — список для перевірки."""
    if value not in (Rating.USEFUL, Rating.WRONG):
        await query.answer()
        return
    rating = Rating(value)
    async with answerer.sessions.begin() as session:
        await session.execute(
            update(QaLog).where(QaLog.id == qa_id).values(rating=rating, rated_at=datetime.now(UTC))
        )
    await query.answer(texts.RATED_USEFUL if rating is Rating.USEFUL else texts.RATED_WRONG)


async def _send_document(
    query: CallbackQuery, answerer: Answerer, settings: Settings, bot: Bot, document_id: int
) -> None:
    """Сам файл документа-джерела (D-28): архівний — зі знімка, від звіра — за file_id."""
    chat_id = query.from_user.id
    async with answerer.sessions() as session:
        document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        await bot.send_message(chat_id, texts.DOCUMENT_GONE)
        return
    if document.storage_path:
        path = settings.archive_dir / document.storage_path
        if path.is_file():
            await bot.send_document(chat_id, FSInputFile(path, filename=document.file_name))
            return
    if document.telegram_file_id:
        await bot.send_document(chat_id, document.telegram_file_id)
        return
    logger.warning("Документ #%s без файлу", document_id)
    await bot.send_message(chat_id, texts.DOCUMENT_GONE)


def build_router() -> Router:
    """Після роутера завантажень: там текст — це ім'я чи назва, якщо бот їх чекає."""
    router = Router(name="questions")
    router.message.register(on_text, F.text, ~F.text.startswith("/"))
    router.callback_query.register(on_button, AskAction.filter())
    return router
