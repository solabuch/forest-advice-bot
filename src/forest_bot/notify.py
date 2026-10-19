"""Сповіщення підписникам (D-26): новий документ чи новий варіант для місця, його частин
і лісу.

Окрема задача черги після коміту документа: збій Telegram для одного звіра не валить
індексацію, а документ, який до розсилки встигли замінити чи видалити, не анонсується.
"""

import logging
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import jobs, keyboards, texts
from forest_bot.models import Document, Subscription
from forest_bot.places import PlaceGraph
from forest_bot.qa.render import short_title, source_of
from forest_bot.repo import subscriptions, users

logger = logging.getLogger(__name__)

NOTIFY = "notify_subscribers"


class Sender(Protocol):
    async def send(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> None: ...


async def enqueue(session: AsyncSession, document: Document, *, replaced: bool) -> None:
    """Документ став чинним — розіслати після коміту. Підписок немає (свіжий бот, імпорт
    архіву) — і задачі немає."""
    if await session.scalar(select(exists().select_from(Subscription))):
        await jobs.enqueue(session, NOTIFY, {"document_id": document.id, "replaced": replaced})


class Notifier:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        sender: Sender,
        graph: PlaceGraph,
        timezone: ZoneInfo,
    ) -> None:
        self.sessions = sessions
        self.sender = sender
        self.graph = graph
        self.timezone = timezone

    async def __call__(self, payload: dict[str, Any]) -> None:
        async with self.sessions() as session:
            document = await session.get(Document, int(payload["document_id"]))
            if document is None or not document.is_current:
                return
            audience = await subscriptions.audience(session, self.graph, document.place_id)
            # Автор і так знає про свій документ
            audience.pop(document.sender_id or 0, None)
            names = await users.names(session, [document.sender_id])
        sent = 0
        for user_id, places in audience.items():
            text, markup = self.notice(document, places, names, replaced=bool(payload["replaced"]))
            try:
                await self.sender.send(user_id, text, markup)
                sent += 1
            except TelegramAPIError as error:
                # Звір заблокував бота чи видалив чат — решті все одно надсилаємо
                logger.warning("Сповіщення не надіслано: %s", error)
        if sent:
            logger.info("Документ #%s: сповіщено підписників — %d", document.id, sent)

    def notice(
        self, document: Document, places: list[str], names: dict[int, str], *, replaced: bool
    ) -> tuple[str, InlineKeyboardMarkup]:
        place = self.graph.get(document.place_id)
        title = short_title(document.title, place)
        template = texts.NOTICE_VERSION if replaced else texts.NOTICE_NEW
        text = template.format(
            place=place.citation, title=title, source=source_of(document, names, self.timezone)
        )
        # Підписка на рукавичку, а порада — для її частини чи лісу: кажемо, чому прийшло
        if also := [self.graph.get(p).citation for p in places if p != document.place_id]:
            text += "\n" + texts.NOTICE_ALSO.format(places=" і ".join(also))
        markup = keyboards.notice_actions(
            document.id, title, [(p, self.graph.get(p).citation) for p in places]
        )
        return text, markup
