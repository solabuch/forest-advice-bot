"""Наскрізно через Telegram (ADR-10): справжній Dispatcher і хендлери, Bot API — фейковий,
БД — справжня. Звір надсилає файл → бот питає ім'я → «Готово»; фото, формат, розмір,
відмова назватися, вибір місця кнопкою."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.filters.callback_data import CallbackData
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    GetFile,
    GetMe,
    LeaveChat,
    SendChatAction,
    SendDocument,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    File,
    Message,
    PhotoSize,
    Update,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import texts
from forest_bot.bot import BotMessenger, build_dispatcher
from forest_bot.ingest.upload import INDEX, PREPARE, UploadPipeline
from forest_bot.jobs import Worker
from forest_bot.keyboards import UploadAction
from forest_bot.models import Upload, UploadStatus
from forest_bot.places import load_places
from forest_bot.repo.places import sync_places
from tests.factories import FAKE_TELEGRAM_TOKEN, make_settings
from tests.integration.test_index import FakeEmbedder

pytestmark = pytest.mark.integration

GRAPH = load_places()
FEES = (
    (Path(__file__).resolve().parents[2] / "data" / "archive")
    .joinpath("Членські внески рукавички Під дубом.txt")
    .read_bytes()
)
ZAYCHYK = 4001
Sessions = async_sessionmaker[AsyncSession]


class FakeBotApi(BaseSession):
    """Bot API без мережі: запам'ятовує виклики, віддає файли з пам'яті."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.files: dict[str, bytes] = {}

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage | SendDocument):
            return Message(
                message_id=len(self.calls),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text if isinstance(method, SendMessage) else None,
            )
        if isinstance(method, GetFile):
            return File(
                file_id=method.file_id, file_unique_id=method.file_id, file_path=method.file_id
            )
        if isinstance(method, AnswerCallbackQuery | EditMessageReplyMarkup | SendChatAction):
            return True
        if isinstance(method, LeaveChat):
            return True
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="Ліс", username="forest_test_bot")
        raise NotImplementedError(type(method).__name__)

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109 — сигнатура з aiogram
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        yield self.files[url.rsplit("/", 1)[-1]]

    async def close(self) -> None:
        return None

    def sent(self) -> list[SendMessage]:
        return [c for c in self.calls if isinstance(c, SendMessage)]


class Forest:
    """Звір, бот і worker в одному тесті."""

    def __init__(self, sessions: Sessions) -> None:
        self.api = FakeBotApi()
        self.bot = Bot(FAKE_TELEGRAM_TOKEN, session=self.api)
        self.pipeline = UploadPipeline(
            sessions, BotMessenger(self.bot), FakeEmbedder(), GRAPH, llm=None
        )
        self.worker = Worker(
            sessions,
            {
                PREPARE: lambda p: self.pipeline.prepare(int(p["upload_id"])),
                INDEX: lambda p: self.pipeline.index(int(p["upload_id"])),
            },
        )
        self.dp = build_dispatcher(
            sessions=sessions, settings=make_settings(), graph=GRAPH, pipeline=self.pipeline
        )
        self.sessions = sessions
        self.update_id = 0
        self.user = User(id=ZAYCHYK, is_bot=False, first_name="Зайчик")
        self.chat = Chat(id=ZAYCHYK, type="private")

    def message(self, **content: Any) -> Message:
        self.update_id += 1
        return Message(
            message_id=self.update_id,
            date=datetime.now(UTC),
            chat=self.chat,
            from_user=self.user,
            **content,
        )

    async def feed(self, **content: Any) -> None:
        self.update_id += 1
        await self.dp.feed_update(self.bot, Update(update_id=self.update_id, **content))

    async def send_file(self, name: str, data: bytes, size: int | None = None) -> None:
        file_id = f"file{self.update_id}"
        self.api.files[file_id] = data
        document = Document(
            file_id=file_id, file_unique_id=file_id, file_name=name, file_size=size or len(data)
        )
        await self.feed(message=self.message(document=document))

    async def say(self, text: str) -> None:
        await self.feed(message=self.message(text=text))

    async def press(self, data: CallbackData) -> None:
        self.update_id += 1
        query = CallbackQuery(
            id=str(self.update_id),
            from_user=self.user,
            chat_instance="chat",
            message=self.message(text="кнопки"),
            data=data.pack(),
        )
        await self.feed(callback_query=query)

    async def work(self) -> None:
        while await self.worker.run_once():
            pass

    def replies(self) -> list[str]:
        return [m.text for m in self.api.sent()]

    async def upload(self) -> Upload:
        async with self.sessions() as session:
            found = await session.scalar(select(Upload).order_by(Upload.id.desc()).limit(1))
            assert found is not None
            return found


@pytest.fixture
async def forest(sessions: Sessions) -> Forest:
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
    return Forest(sessions)


async def test_first_document_asks_name_then_indexes(forest: Forest) -> None:
    await forest.send_file("внески.txt", FEES)
    assert forest.replies() == [texts.ASK_NAME]
    assert (await forest.upload()).status is UploadStatus.NEEDS_NAME

    await forest.say("Зайчик Сірий")
    assert forest.replies()[-1] == texts.NAME_SAVED.format(name="Зайчик Сірий")
    await forest.work()
    assert forest.replies()[-1] == texts.READY.format(
        title="Членські внески рукавички «Під дубом»", place="рукавички «Під дубом»"
    )
    assert (await forest.upload()).status is UploadStatus.DONE


async def test_photo_is_not_a_document(forest: Forest) -> None:
    photo = [PhotoSize(file_id="p", file_unique_id="p", width=10, height=10)]
    await forest.feed(message=forest.message(photo=photo))
    assert forest.replies() == [texts.PHOTO_NOT_ACCEPTED]


async def test_unsupported_format(forest: Forest) -> None:
    await forest.send_file("пісня.mp3", b"ID3")
    assert forest.replies() == [texts.UNSUPPORTED]


async def test_file_over_limit_is_explained(forest: Forest) -> None:
    await forest.send_file("путівник.pdf", b"%PDF", size=25 * 1024 * 1024)
    assert forest.replies() == [texts.TOO_BIG.format(limit=20)]


async def test_refusing_name_cancels_document(forest: Forest) -> None:
    await forest.send_file("внески.txt", FEES)
    await forest.press(UploadAction(id=0, action="refuse_name"))
    assert forest.replies()[-1] == texts.NAME_REFUSED
    assert (await forest.upload()).status is UploadStatus.CANCELLED


async def test_place_chosen_with_button(forest: Forest) -> None:
    await forest.send_file("горіхи.txt", "Горіхи\n\nГоріхи збирайте, коли самі падають.".encode())
    await forest.say("Білка")
    await forest.work()
    upload = await forest.upload()
    assert upload.status is UploadStatus.NEEDS_PLACE
    assert forest.replies()[-1] == texts.ASK_PLACE.format(title="Горіхи")

    await forest.press(UploadAction(id=upload.id, action="place", value="u-lishchyni"))
    await forest.work()
    assert forest.replies()[-1] == texts.READY.format(title="Горіхи", place="рукавички «У ліщині»")


async def ready_fees(forest: Forest) -> Upload:
    """Зайчик назвався й надіслав внески — документ уже шукається."""
    await forest.send_file("внески.txt", FEES)
    await forest.say("Зайчик")
    await forest.work()
    return await forest.upload()


async def test_new_version_with_buttons(forest: Forest) -> None:
    first = await ready_fees(forest)
    await forest.send_file(
        "внески-нові.txt", FEES.replace("4 жолуді".encode(), "6 жолудів".encode())
    )
    await forest.work()
    second = await forest.upload()
    assert second.status is UploadStatus.NEEDS_CHOICE
    # Чужий id замість кандидата — ігнорується
    await forest.press(UploadAction(id=second.id, action="replace", value="999999"))
    assert (await forest.upload()).status is UploadStatus.NEEDS_CHOICE

    await forest.press(UploadAction(id=second.id, action="replace", value=str(first.document_id)))
    await forest.work()
    assert forest.replies()[-1].startswith("Готово ✅")
    assert "замість попереднього варіанта" in forest.replies()[-1]


async def test_separate_document_with_button(forest: Forest) -> None:
    await ready_fees(forest)
    await forest.send_file("внески-копія.txt", FEES + b"\n")
    await forest.work()
    second = await forest.upload()
    await forest.press(UploadAction(id=second.id, action="separate"))
    await forest.work()
    assert (await forest.upload()).status is UploadStatus.DONE
    assert "замість" not in forest.replies()[-1]


async def test_change_title_and_place(forest: Forest) -> None:
    upload = await ready_fees(forest)
    await forest.press(UploadAction(id=upload.id, action="title"))
    assert forest.replies()[-1] == texts.ASK_TITLE.format(title=upload.title)
    await forest.say("Внески дубової рукавички")
    assert forest.replies()[-1] == texts.TITLE_SAVED.format(title="Внески дубової рукавички")

    await forest.press(UploadAction(id=upload.id, action="move"))
    assert forest.replies()[-1] == texts.ASK_MOVE.format(title="Внески дубової рукавички")
    await forest.press(UploadAction(id=upload.id, action="moveto", value="deciduous"))
    assert forest.replies()[-1] == texts.PLACE_SAVED.format(
        title="Внески дубової рукавички", place="листяного лісу"
    )


async def test_cancel_while_choosing_place(forest: Forest) -> None:
    await forest.send_file("горіхи.txt", "Горіхи\n\nГоріхи збирайте восени.".encode())
    await forest.say("Білка")
    await forest.work()
    upload = await forest.upload()
    await forest.press(UploadAction(id=upload.id, action="cancel"))
    assert forest.replies()[-1] == texts.CANCELLED.format(title="Горіхи")
    assert (await forest.upload()).status is UploadStatus.CANCELLED


async def test_invalid_name_is_asked_again(forest: Forest) -> None:
    await forest.send_file("внески.txt", FEES)
    await forest.say("123")
    assert "літер" in forest.replies()[-1]
    assert (await forest.upload()).status is UploadStatus.NEEDS_NAME


async def test_hourly_limit(forest: Forest, monkeypatch: pytest.MonkeyPatch) -> None:
    forest.dp.workflow_data["settings"] = make_settings(uploads_per_hour=1)
    await forest.send_file("перший.txt", FEES)
    await forest.send_file("другий.txt", FEES + b" ")
    assert forest.replies()[-1] == texts.TOO_MANY.format(limit=1)
