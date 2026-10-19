"""Питання наскрізно через Telegram (ADR-10): справжній Dispatcher, фейкові Bot API і Groq,
справжня БД. Відповідь із кнопками, місце кнопкою (D-06), уточнення (D-24), оцінка (D-50),
файл джерела (D-28), привітання без LLM (D-31)."""

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from aiogram.methods import SendChatAction, SendDocument, SendMessage
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import texts
from forest_bot.handlers.questions import DialogMemory
from forest_bot.keyboards import AskAction
from forest_bot.models import Document, QaLog, Rating
from forest_bot.qa.answer import Answerer
from forest_bot.repo.places import sync_places
from tests.factories import make_settings
from tests.integration.test_answer import FakeGroq, QueryVectors, every_fragment
from tests.integration.test_search import add
from tests.integration.test_upload_flow import GRAPH, Forest

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]
FEES = "Членські внески рукавички «Під дубом»"
ARCHIVE = Path(__file__).resolve().parents[2] / "data" / "archive"
SIGNED = "— з документа «Членські внески» рукавички «Під дубом»"


def groq_without_place_guess(user: str) -> dict[str, Any]:
    """Промпт місця — «не знаю» (тоді бот питає кнопками), промпт відповіді — з фрагментів."""
    if user.startswith("<текст>"):
        return {"place_id": None, "confidence": 0.0}
    return every_fragment(user)


class Asking(Forest):
    def __init__(self, sessions: Sessions) -> None:
        super().__init__(sessions)
        self.groq = FakeGroq(groq_without_place_guess)
        self.answerer = Answerer(
            sessions, QueryVectors(), GRAPH, self.groq.client(), ZoneInfo("Europe/Kyiv")
        )
        self.dp.workflow_data.update(answerer=self.answerer, memory=DialogMemory())
        self.last_question: Message | None = None

    async def ask(self, text: str) -> None:
        self.last_question = self.message(text=text)
        await self.feed(message=self.last_question)

    async def tap(self, data: AskAction, *, under_question: bool = True) -> None:
        """Кнопка під повідомленням бота, що відповідало на питання звіра."""
        self.update_id += 1
        bot_message = self.message(text="кнопки")
        if under_question and self.last_question:
            bot_message = bot_message.model_copy(update={"reply_to_message": self.last_question})
        query = CallbackQuery(
            id=str(self.update_id),
            from_user=self.user,
            chat_instance="chat",
            message=bot_message,
            data=data.pack(),
        )
        await self.feed(callback_query=query)

    def last(self) -> SendMessage:
        return self.api.sent()[-1]

    def buttons(self) -> list[str]:
        markup = self.last().reply_markup
        assert isinstance(markup, InlineKeyboardMarkup)
        return [button.text for row in markup.inline_keyboard for button in row]


@pytest.fixture
async def asking(sessions: Sessions) -> Asking:
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
        await add(session, "pid-dubom", FEES)
    return Asking(sessions)


async def test_answer_has_signature_and_buttons(asking: Asking) -> None:
    # R-01, R-02, R-09: питання звичайним текстом → відповідь із документом-джерелом
    await asking.ask("Скільки внесок під дубом?")
    assert asking.last().text.endswith(SIGNED)
    assert asking.buttons() == ["📄 Членські внески", texts.USEFUL, texts.WRONG]
    # Поки шукає — «друкує…» (N-03)
    assert any(isinstance(call, SendChatAction) for call in asking.api.calls)


async def test_no_place_asks_with_buttons_then_answers(asking: Asking) -> None:
    # R-18: місця того, хто питає, бот не підставляє — питає кнопками
    await asking.ask("Скільки внесок?")
    assert asking.last().text == texts.ASK_QUESTION_PLACE
    assert "«Під дубом»" in asking.buttons()

    await asking.tap(AskAction(action="place", value="pid-dubom"))
    assert asking.last().text.endswith(SIGNED)


async def test_follow_up_keeps_place_and_offers_another(asking: Asking) -> None:
    await asking.ask("Скільки внесок під дубом?")
    await asking.ask("А до якого числа?")
    assert asking.last().text.startswith(texts.FROM_CONTEXT.format(place="рукавички «Під дубом»"))
    assert texts.OTHER_PLACE in asking.buttons()
    # Модель бачить, уточненням чого є питання
    assert "<попереднє>\nСкільки внесок під дубом?\n</попереднє>" in asking.groq.prompts[-1]

    await asking.tap(AskAction(action="other"))
    assert asking.last().text == texts.ASK_QUESTION_PLACE


async def test_wrong_answer_goes_to_review(asking: Asking) -> None:
    await asking.ask("Скільки внесок під дубом?")
    async with asking.sessions() as session:
        log = await session.scalar(select(QaLog))
    assert log is not None
    await asking.tap(AskAction(action="rate", value="wrong", id=log.id))
    async with asking.sessions() as session:
        rated = await session.get_one(QaLog, log.id)
    assert rated.rating is Rating.WRONG


async def test_source_button_sends_the_file(asking: Asking) -> None:
    # R-04: документ доступний кожному, хто питає
    # Тести працюють в окремій теці — шлях до знімка архіву абсолютний
    asking.dp.workflow_data["settings"] = make_settings(archive_dir=ARCHIVE)
    async with asking.sessions.begin() as session:
        await session.execute(
            update(Document)
            .where(Document.title == FEES)
            .values(storage_path="Членські внески рукавички Під дубом.txt")
        )
    await asking.ask("Скільки внесок під дубом?")
    async with asking.sessions() as session:
        document = await session.scalar(select(Document))
    assert document is not None
    await asking.tap(AskAction(action="doc", id=document.id))
    assert any(isinstance(call, SendDocument) for call in asking.api.calls)


async def test_greeting_and_thanks_without_llm(asking: Asking) -> None:
    await asking.ask("Привіт!")
    assert asking.last().text == texts.GREETING
    await asking.ask("Дякую")
    assert asking.last().text == texts.THANKS
    assert asking.groq.prompts == []


async def test_advice_as_text_asks_for_a_file(asking: Asking) -> None:
    await asking.ask("Гриби збирайте після дощу. " * 15)
    assert asking.last().text == texts.ADVICE_AS_TEXT
