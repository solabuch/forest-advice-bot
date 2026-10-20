"""Доступ за запрошенням (D-54, ADR-17) і лише особисті чати (R-25) — через Dispatcher."""

from datetime import UTC, datetime

import pytest
from aiogram.methods import AnswerCallbackQuery, LeaveChat, SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    User,
)
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import texts
from forest_bot.access import invite_code
from forest_bot.keyboards import AskAction
from forest_bot.models import JoinedVia
from forest_bot.models import User as Animal
from tests.factories import make_settings
from tests.integration.test_upload_flow import Forest

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]
STRANGER = User(id=4002, is_bot=False, first_name="Мисливець")
BOT = User(id=1, is_bot=True, first_name="Ліс")


class Gate(Forest):
    async def send_as(self, user: User, text: str, chat: Chat | None = None) -> None:
        self.update_id += 1
        message = Message(
            message_id=self.update_id,
            date=datetime.now(UTC),
            chat=chat or Chat(id=user.id, type="private"),
            from_user=user,
            text=text,
        )
        await self.feed(message=message)

    def last_to(self, chat_id: int) -> SendMessage | None:
        sent = [m for m in self.api.sent() if int(m.chat_id) == chat_id]
        return sent[-1] if sent else None

    async def animal(self, user_id: int) -> Animal | None:
        async with self.sessions() as session:
            return await session.get(Animal, user_id)

    async def code(self) -> str:
        async with self.sessions.begin() as session:
            return await invite_code(session, make_settings())


@pytest.fixture
async def gate(sessions: Sessions) -> Gate:
    return Gate(sessions)


async def test_first_animal_of_a_fresh_bot_becomes_member(gate: Gate) -> None:
    await gate.say("/start")
    assert gate.replies() == [texts.START]
    animal = await gate.animal(gate.user.id)
    assert animal is not None
    assert animal.joined_via is JoinedVia.FIRST


async def test_stranger_needs_an_invitation(gate: Gate) -> None:
    await gate.say("/start")
    await gate.send_as(STRANGER, "Де стежка до рукавички під дубом?")
    sent = gate.last_to(STRANGER.id)
    assert sent is not None
    assert sent.text == texts.NOT_MEMBER
    assert await gate.animal(STRANGER.id) is None


async def test_invitation_link_admits(gate: Gate) -> None:
    await gate.say("/start")
    await gate.send_as(STRANGER, "/start not-the-code")
    stale = gate.last_to(STRANGER.id)
    assert stale is not None
    assert stale.text == texts.INVITE_EXPIRED

    await gate.send_as(STRANGER, f"/start {await gate.code()}")
    welcome = gate.last_to(STRANGER.id)
    assert welcome is not None
    assert welcome.text == texts.START
    animal = await gate.animal(STRANGER.id)
    assert animal is not None
    assert animal.joined_via is JoinedVia.INVITE


async def test_code_typed_in_cyrillic_is_refused_politely(gate: Gate) -> None:
    # D-54: «/start привіт» набрали вручну — відмова, а не виняток у middleware
    await gate.say("/start")
    await gate.send_as(STRANGER, "/start привіт")
    sent = gate.last_to(STRANGER.id)
    assert sent is not None
    assert sent.text == texts.INVITE_EXPIRED
    assert await gate.animal(STRANGER.id) is None


async def test_member_gets_a_link_that_can_be_forwarded(gate: Gate) -> None:
    # R-03: запрошує будь-який мешканець — адмінів немає
    await gate.say("/start")
    await gate.say("/invite")
    sent = gate.api.sent()[-1]
    assert f"https://t.me/forest_test_bot?start={await gate.code()}" in sent.text
    # Посилання треба переслати сусідові — тож не захищене
    assert sent.protect_content is False


async def test_code_from_settings_wins(gate: Gate) -> None:
    async with gate.sessions.begin() as session:
        code = await invite_code(session, make_settings(invite_code=SecretStr("лісовий-2026")))
    assert code == "лісовий-2026"


async def test_buttons_of_strangers_are_refused(gate: Gate) -> None:
    await gate.say("/start")
    gate.update_id += 1
    query = CallbackQuery(
        id="q",
        from_user=STRANGER,
        chat_instance="chat",
        data=AskAction(action="doc", id=1).pack(),
    )
    await gate.feed(callback_query=query)
    answer = next(c for c in gate.api.calls if isinstance(c, AnswerCallbackQuery))
    assert (answer.text, answer.show_alert) == (texts.NOT_MEMBER, True)


async def test_group_is_left_and_ignored(gate: Gate) -> None:
    group = Chat(id=-100500, type="group")
    await gate.send_as(gate.user, "Скільки внесок під дубом?", chat=group)
    assert gate.last_to(group.id) is None
    gate.update_id += 1
    added = ChatMemberUpdated(
        chat=group,
        from_user=gate.user,
        date=datetime.now(UTC),
        old_chat_member=ChatMemberLeft(user=BOT),
        new_chat_member=ChatMemberMember(user=BOT),
    )
    await gate.feed(my_chat_member=added)
    farewell = gate.last_to(group.id)
    assert farewell is not None
    assert farewell.text == texts.PRIVATE_ONLY
    assert any(isinstance(c, LeaveChat) for c in gate.api.calls)
