"""Підписка на місце (D-26) наскрізно: кнопка вмикає й вимикає, сповіщення отримують
підписники місць, для яких документ доречний (R-10, R-17, R-27), відписка — однією кнопкою."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import update

from forest_bot import notify, texts
from forest_bot.keyboards import SubscribeAction, UploadAction
from forest_bot.models import Document, Subscription
from forest_bot.models import User as Animal
from forest_bot.repo import subscriptions
from forest_bot.repo.places import sync_places
from tests.integration.test_library_flow import TITLE, UNDER_OAK, archived
from tests.integration.test_search import add
from tests.integration.test_upload_flow import (
    FEES,
    GRAPH,
    ZAYCHYK,
    Forest,
    Sessions,
    ready_fees,
)

pytestmark = pytest.mark.integration

BILKA, LYS, SOVA = 4101, 4102, 4103


@pytest.fixture
async def forest(sessions: Sessions) -> Forest:
    """Сусіди вже в боті: Білка підписана на «Під дубом», Лис — на хвойний ліс,
    Сова — на листяний; Зайчик — теж на «Під дубом»."""
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
        now = datetime.now(UTC)
        for user_id, place_id in (
            (BILKA, "pid-dubom"),
            (LYS, "coniferous"),
            (SOVA, "deciduous"),
            (ZAYCHYK, "pid-dubom"),
        ):
            session.add(Animal(id=user_id, member_since=now))
            await session.flush()
            session.add(Subscription(user_id=user_id, place_id=place_id))
    return Forest(sessions)


def sent_to(forest: Forest, user_id: int) -> list[SendMessage]:
    return [m for m in forest.api.sent() if int(m.chat_id) == user_id]


def labels(markup: object) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [button.text for row in markup.inline_keyboard for button in row]


def alert(forest: Forest) -> str | None:
    return [c for c in forest.api.calls if isinstance(c, AnswerCallbackQuery)][-1].text


def today() -> str:
    return datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%d.%m.%Y")


async def test_subscribe_and_unsubscribe_with_the_same_button(forest: Forest) -> None:
    await forest.say("/subscribe")
    assert forest.replies()[-1] == texts.SUBSCRIBE
    assert "✅ «Під дубом»" in labels(forest.api.sent()[-1].reply_markup)

    await forest.press(SubscribeAction(action="toggle", value="u-lishchyni"))
    assert alert(forest) == texts.SUBSCRIBED.format(place="рукавички «У ліщині»")
    edit = [c for c in forest.api.calls if isinstance(c, EditMessageReplyMarkup)][-1]
    assert "✅ «У ліщині»" in labels(edit.reply_markup)

    await forest.press(SubscribeAction(action="toggle", value="pid-dubom"))
    assert alert(forest) == texts.UNSUBSCRIBED.format(place=UNDER_OAK)
    async with forest.sessions() as session:
        assert await subscriptions.places_of(session, ZAYCHYK) == {"u-lishchyni"}


async def test_new_document_reaches_subscribers_of_its_place(forest: Forest) -> None:
    await ready_fees(forest)
    [notice] = sent_to(forest, BILKA)
    source = texts.SENT_BY.format(who="Зайчик", date=today())
    assert notice.text == texts.NOTICE_NEW.format(
        place=UNDER_OAK, title="Членські внески", source=source
    )
    assert labels(notice.reply_markup) == [
        "📄 Членські внески",
        texts.UNSUBSCRIBE_BUTTON.format(place=UNDER_OAK),
    ]
    # Порада рукавички не стосується частин лісу (R-17); автор і так знає про свою
    assert sent_to(forest, SOVA) == []
    assert sent_to(forest, LYS) == []
    assert not any(texts.NOTICE_NEW[:10] in (m.text or "") for m in sent_to(forest, ZAYCHYK))


async def test_forest_advice_reaches_everyone_and_says_why(forest: Forest) -> None:
    async with forest.sessions.begin() as session:
        document = await add(session, "forest", "Пожежа")
        await notify.enqueue(session, document, replaced=False)
    await forest.work()
    new = texts.NOTICE_NEW.format(place="всього лісу", title="Пожежа", source=texts.FROM_ARCHIVE)
    assert [m.text for m in sent_to(forest, BILKA)] == [
        f"{new}\n{texts.NOTICE_ALSO.format(places=UNDER_OAK)}"
    ]
    assert [m.text for m in sent_to(forest, LYS)] == [
        f"{new}\n{texts.NOTICE_ALSO.format(places='хвойного лісу')}"
    ]


async def test_new_version_notice(forest: Forest) -> None:
    previous = await archived(forest, "pid-dubom", TITLE)
    await forest.send_file("внески.txt", FEES)
    await forest.say("Зайчик")
    await forest.work()
    upload = await forest.upload()
    await forest.press(UploadAction(id=upload.id, action="replace", value=str(previous)))
    await forest.work()
    [notice] = sent_to(forest, BILKA)
    assert notice.text == texts.NOTICE_VERSION.format(
        place=UNDER_OAK,
        title="Членські внески",
        source=texts.SENT_BY.format(who="Зайчик", date=today()),
    )


async def test_replaced_before_sending_is_not_announced(forest: Forest) -> None:
    async with forest.sessions.begin() as session:
        document = await add(session, "pid-dubom", "Внески 2025")
        await notify.enqueue(session, document, replaced=False)
        await session.execute(
            update(Document).where(Document.id == document.id).values(is_current=False)
        )
    await forest.work()
    assert sent_to(forest, BILKA) == []


async def test_unsubscribe_from_notice(forest: Forest) -> None:
    await forest.press(SubscribeAction(action="off", value="pid-dubom"))
    assert alert(forest) == texts.UNSUBSCRIBED.format(place=UNDER_OAK)
    async with forest.sessions() as session:
        assert await subscriptions.places_of(session, ZAYCHYK) == set()
