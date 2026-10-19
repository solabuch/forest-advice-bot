"""Telegram-частина: диспетчер, хендлери, полінг."""

import asyncio
import contextlib
import logging
import time

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, Message

from forest_bot import db, jobs, startup, texts
from forest_bot.config import Settings
from forest_bot.embeddings import load_embedder
from forest_bot.handlers import questions, upload
from forest_bot.health import heartbeat_loop
from forest_bot.ingest.archive import import_archive
from forest_bot.ingest.upload import INDEX, PREPARE, UploadPipeline
from forest_bot.llm.client import GroqClient
from forest_bot.llm.profiles import DEFAULT_CHAIN
from forest_bot.places import load_places
from forest_bot.qa.answer import Answerer
from forest_bot.repo.places import sync_places

logger = logging.getLogger(__name__)


async def on_start(message: Message) -> None:
    await message.answer(texts.START)


def build_dispatcher(**dependencies: object) -> Dispatcher:
    """Залежності (конвеєр, відповідач, налаштування) хендлери отримують аргументами за
    назвою. Порядок роутерів важливий: текст спершу перевіряє роутер завантажень — він
    чекає ім'я чи назву, — а все інше йде як питання."""
    dp = Dispatcher()
    dp.workflow_data.update(dependencies)
    core = Router(name="core")
    core.message.register(on_start, CommandStart())
    core.message.register(on_start, Command("help"))
    dp.include_router(core)
    dp.include_router(upload.build_router())
    dp.include_router(questions.build_router())
    return dp


class BotMessenger:
    """Telegram для фонових задач: повідомлення звірові й файл за file_id."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> None:
        await self._bot.send_message(chat_id, text, reply_markup=markup)

    async def download(self, file_id: str) -> bytes:
        buffer = await self._bot.download(file_id)
        if buffer is None:
            raise RuntimeError(f"Telegram не віддав файл {file_id}")
        return buffer.read()


def build_bot(settings: Settings) -> Bot:
    # protect_content: поради й документи не пересилаються й не зберігаються (D-54)
    return Bot(
        settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(protect_content=True),
    )


async def _another_instance_running(bot: Bot) -> bool:
    """Інший процес уже полить цей токен → Telegram відповідає Conflict."""
    try:
        # Без offset — нічого не підтверджує й не губить апдейти
        await bot.get_updates(limit=1, timeout=0)
    except TelegramConflictError:
        return True
    return False


ARCHIVE_IMPORT = "archive_import"


async def run(settings: Settings) -> int:
    """Самоперевірка → черга задач (імпорт архіву) → полінг. Повертає код виходу.

    ROLE=all — усе в одному процесі; bot — лише полінг; worker — лише черга.
    """
    started = time.monotonic()
    results = await startup.run_checks(settings)
    if code := startup.exit_code(results):
        return code

    engine = db.create_engine(settings)
    sessions = db.session_factory(engine)
    graph = load_places(settings.places_file)
    async with sessions.begin() as session:
        await sync_places(session, graph)
    embedder = load_embedder(settings.embedding_model_dir)
    llm = GroqClient(
        settings.groq_api_key.get_secret_value(), settings.groq_models or DEFAULT_CHAIN
    )

    async def archive(_: dict[str, object]) -> None:
        await import_archive(sessions, settings.archive_dir, embedder, graph, llm)

    bot = build_bot(settings)
    pipeline = UploadPipeline(sessions, BotMessenger(bot), embedder, graph, llm)

    async def prepare(payload: dict[str, object]) -> None:
        await pipeline.prepare(int(str(payload["upload_id"])))

    async def index(payload: dict[str, object]) -> None:
        await pipeline.index(int(str(payload["upload_id"])))

    worker = jobs.Worker(sessions, {ARCHIVE_IMPORT: archive, PREPARE: prepare, INDEX: index})
    pipeline.wake = worker.wake
    background = [asyncio.create_task(heartbeat_loop())]
    if settings.role in ("all", "worker"):
        # Архів — на кожному старті: нові й змінені файли знімка, решта пропускається
        async with sessions.begin() as session:
            if not await jobs.has_pending(session, ARCHIVE_IMPORT):
                await jobs.enqueue(session, ARCHIVE_IMPORT)
        background.append(asyncio.create_task(worker.run()))

    try:
        if settings.role == "worker":
            logger.info("Worker готовий за %.1f с", time.monotonic() - started)
            await asyncio.gather(*background)
            return 0
        # Вебхук від іншого запуску блокує полінг — прибираємо, апдейти не губимо
        await bot.delete_webhook(drop_pending_updates=False)
        if await _another_instance_running(bot):
            logger.error("З цим токеном уже працює інший екземпляр бота — зупини його")
            return 2
        me = await bot.get_me()
        logger.info("Бот @%s готовий за %.1f с", me.username, time.monotonic() - started)
        answerer = Answerer(sessions, embedder, graph, llm, settings.timezone)
        dispatcher = build_dispatcher(
            pipeline=pipeline,
            settings=settings,
            answerer=answerer,
            memory=questions.DialogMemory(),
        )
        await dispatcher.start_polling(bot)
    finally:
        for task in background:
            task.cancel()
        for task in background:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await bot.session.close()
        await llm.aclose()
        await engine.dispose()
    return 0
