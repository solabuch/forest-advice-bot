"""Ім'я й «Забути мене» через Dispatcher: зміна імені з підтвердженням (D-29), зайняте
ім'я (D-44), «Забути мене» (D-51)."""

from datetime import UTC, datetime

import pytest
from aiogram.types import CallbackQuery, ForceReply, Message, User
from sqlalchemy import func, select

from forest_bot import texts
from forest_bot.keyboards import AccountAction, LibraryAction, UploadAction
from forest_bot.models import Chunk, Subscription, Upload, UploadStatus
from forest_bot.models import User as Animal
from forest_bot.repo.places import sync_places
from tests.integration.test_library_flow import TITLE, archived, stored
from tests.integration.test_upload_flow import FEES, GRAPH, Forest, Sessions, ready_fees

pytestmark = pytest.mark.integration

BOT = User(id=1, is_bot=True, first_name="Ліс")


@pytest.fixture
async def forest(sessions: Sessions) -> Forest:
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
    return Forest(sessions)


def from_bot(forest: Forest, text: str, reply_to: Message | None = None) -> Message:
    forest.update_id += 1
    return Message(
        message_id=forest.update_id,
        date=datetime.now(UTC),
        chat=forest.chat,
        from_user=BOT,
        text=text,
        reply_to_message=reply_to,
    )


async def reply_to_prompt(forest: Forest, prompt: str, text: str) -> None:
    """Звір відповідає на прохання бота (ForceReply)."""
    await forest.feed(message=forest.message(text=text, reply_to_message=from_bot(forest, prompt)))


async def confirm(forest: Forest, action: str, typed: str = "") -> None:
    """Кнопка під підтвердженням бота, що відповідало на повідомлення звіра з ім'ям."""
    confirmation = from_bot(forest, "?", forest.message(text=typed))
    forest.update_id += 1
    query = CallbackQuery(
        id=str(forest.update_id),
        from_user=forest.user,
        chat_instance="chat",
        message=confirmation,
        data=AccountAction(action=action).pack(),
    )
    await forest.feed(callback_query=query)


async def animal(forest: Forest) -> Animal:
    async with forest.sessions() as session:
        return await session.get_one(Animal, forest.user.id)


async def test_rename_with_confirmation(forest: Forest) -> None:
    await ready_fees(forest)
    await forest.say("/rename")
    prompt = texts.ASK_NEW_NAME.format(name="Зайчик")
    assert forest.replies()[-1] == prompt
    assert isinstance(forest.api.sent()[-1].reply_markup, ForceReply)

    await reply_to_prompt(forest, prompt, "Зайчик Сірий")
    assert forest.replies()[-1] == texts.CONFIRM_RENAME.format(name="Зайчик Сірий")
    await confirm(forest, "rename_yes", "Зайчик Сірий")
    assert forest.replies()[-1] == texts.RENAMED.format(name="Зайчик Сірий")
    assert (await animal(forest)).real_name == "Зайчик Сірий"

    # Нове ім'я — в усіх документах (D-29)
    await forest.press(LibraryAction(action="place", value="pid-dubom"))
    assert "надіслано: Зайчик Сірий" in forest.replies()[-1]


async def test_taken_name_asks_for_a_distinguishing_word(forest: Forest) -> None:
    async with forest.sessions.begin() as session:
        session.add(Animal(id=4100, real_name="Білка", name_key="білка"))
    await forest.say("/rename")
    await reply_to_prompt(forest, texts.ASK_FIRST_NAME, "білка")
    assert "уже має інший звір" in forest.replies()[-1]
    assert texts.RENAME_PROMPT in forest.replies()[-1]
    assert (await animal(forest)).real_name is None


async def test_rename_cancelled(forest: Forest) -> None:
    await ready_fees(forest)
    await confirm(forest, "rename_no", "Інше Ім'я")
    assert forest.replies()[-1] == texts.RENAME_KEPT
    assert (await animal(forest)).real_name == "Зайчик"


async def test_first_name_releases_waiting_document(forest: Forest) -> None:
    await forest.send_file("внески.txt", FEES)
    assert forest.replies()[-1] == texts.ASK_NAME
    await forest.say("/rename")
    # Відповідь на прохання про ім'я — не назва документа й не питання
    await reply_to_prompt(forest, texts.ASK_FIRST_NAME, "Зайчик")
    assert forest.replies()[-1] == texts.CONFIRM_RENAME.format(name="Зайчик")
    await confirm(forest, "rename_yes", "Зайчик")
    await forest.work()
    assert forest.replies()[-1].startswith("Готово ✅")


async def test_forget_me(forest: Forest) -> None:
    # Зайчик замінив архівні внески своїм варіантом і підписався на «Під дубом»
    previous = await archived(forest, "pid-dubom", TITLE)
    await forest.send_file("внески.txt", FEES)
    await forest.say("Зайчик")
    await forest.work()
    upload = await forest.upload()
    await forest.press(UploadAction(id=upload.id, action="replace", value=str(previous)))
    await forest.work()
    upload = await forest.upload()
    assert upload.document_id is not None
    async with forest.sessions.begin() as session:
        session.add(Subscription(user_id=forest.user.id, place_id="pid-dubom"))

    await forest.say("/forget")
    assert forest.replies()[-1] == texts.FORGET_ASK.format(count=1)
    await confirm(forest, "forget_yes")
    assert forest.replies()[-1] == texts.FORGOTTEN.format(count=1)

    assert (await animal(forest)).real_name is None
    assert (await stored(forest, upload.document_id)).deleted_at is not None
    # Замінене знову діє (D-43)
    assert (await stored(forest, previous)).is_current
    async with forest.sessions() as session:
        chunks = await session.scalar(
            select(func.count()).where(Chunk.document_id == upload.document_id)
        )
        subscriptions = await session.scalar(select(func.count()).select_from(Subscription))
        parsed = await session.scalar(select(Upload.parsed).where(Upload.id == upload.id))
    assert (chunks, subscriptions, parsed) == (0, 0, None)


async def test_forget_cancels_documents_in_progress(forest: Forest) -> None:
    await forest.send_file("горіхи.txt", "Горіхи\n\nГоріхи збирайте восени.".encode())
    await forest.say("Білка")
    await forest.work()
    assert (await forest.upload()).status is UploadStatus.NEEDS_PLACE
    await confirm(forest, "forget_yes")
    assert (await forest.upload()).status is UploadStatus.CANCELLED


async def test_forget_kept(forest: Forest) -> None:
    await ready_fees(forest)
    await confirm(forest, "forget_no")
    assert forest.replies()[-1] == texts.FORGET_KEPT
    assert (await animal(forest)).real_name == "Зайчик"
