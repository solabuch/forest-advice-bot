"""Telegram-частина: диспетчер, хендлери, полінг."""

import asyncio
import contextlib
import logging
import time

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramConflictError
from aiogram.filters import CommandStart
from aiogram.types import Message

from forest_bot import db, startup
from forest_bot.config import Settings
from forest_bot.health import heartbeat_loop
from forest_bot.places import load_places
from forest_bot.repo.places import sync_places

logger = logging.getLogger(__name__)

router = Router(name="core")


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    # Тимчасове привітання; повний онбординг — разом із UX-діалогами
    await message.answer(
        "Привіт! Я бот порад для звірів лісу. Скоро вмітиму відповідати на питання."
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


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


async def run(settings: Settings) -> int:
    """Самоперевірка → полінг. Повертає код виходу."""
    started = time.monotonic()
    results = await startup.run_checks(settings)
    if code := startup.exit_code(results):
        return code

    engine = db.create_engine(settings)
    sessions = db.session_factory(engine)
    async with sessions.begin() as session:
        await sync_places(session, load_places(settings.places_file))

    bot = build_bot(settings)
    heartbeat = asyncio.create_task(heartbeat_loop())
    try:
        # Вебхук від іншого запуску блокує полінг — прибираємо, апдейти не губимо
        await bot.delete_webhook(drop_pending_updates=False)
        if await _another_instance_running(bot):
            logger.error("З цим токеном уже працює інший екземпляр бота — зупини його")
            return 2
        me = await bot.get_me()
        logger.info("Бот @%s готовий за %.1f с", me.username, time.monotonic() - started)
        await build_dispatcher().start_polling(bot)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await bot.session.close()
        await engine.dispose()
    return 0
