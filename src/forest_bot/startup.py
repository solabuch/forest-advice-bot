"""Самоперевірка на старті: що працює, а що ні — людською мовою, з часом кожного кроку."""

import asyncio
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg
import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from forest_bot.config import Settings

logger = logging.getLogger(__name__)

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
TESSERACT_LANGS = ("ukr", "eng")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    # fatal=False — попередження: бот працює, але частина можливостей недоступна
    fatal: bool = True
    seconds: float = 0.0


async def check_database(settings: Settings) -> CheckResult:
    try:
        conn = await asyncpg.connect(settings.asyncpg_dsn, timeout=10)
    except (OSError, asyncpg.PostgresError, TimeoutError) as error:
        return CheckResult("БД", False, f"немає з'єднання з базою ({type(error).__name__})")
    try:
        version = await conn.fetchval("SHOW server_version")
    finally:
        await conn.close()
    return CheckResult("БД", True, f"Postgres {version}")


async def _tesseract_langs(binary: str) -> set[str]:
    proc = await asyncio.create_subprocess_exec(
        binary,
        "--list-langs",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    # Перший рядок — заголовок «List of available languages…»
    return {line.strip() for line in out.decode(errors="replace").splitlines()[1:]}


async def check_tesseract() -> CheckResult:
    binary = shutil.which("tesseract")
    if binary is None:
        return CheckResult("OCR", False, "tesseract не знайдено — скани не читатимуться", False)
    missing = [lang for lang in TESSERACT_LANGS if lang not in await _tesseract_langs(binary)]
    if missing:
        detail = f"немає мовних моделей tesseract: {', '.join(missing)}"
        return CheckResult("OCR", False, detail, False)
    return CheckResult("OCR", True, f"tesseract ({', '.join(TESSERACT_LANGS)})")


async def check_antiword() -> CheckResult:
    if shutil.which("antiword") is None:
        return CheckResult(".doc", False, "antiword не знайдено — .doc не читатимуться", False)
    return CheckResult(".doc", True, "antiword")


async def check_telegram(settings: Settings) -> CheckResult:
    bot = Bot(settings.telegram_bot_token.get_secret_value())
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        detail = "TELEGRAM_BOT_TOKEN недійсний — Telegram його не приймає; онови токен у @BotFather"
        return CheckResult("Telegram", False, detail)
    except TelegramNetworkError:
        return CheckResult("Telegram", False, "немає зв'язку з api.telegram.org")
    finally:
        await bot.session.close()
    return CheckResult("Telegram", True, f"@{me.username}")


async def check_groq(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> CheckResult:
    headers = {"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10) as client:
            response = await client.get(GROQ_MODELS_URL, headers=headers)
    except httpx.HTTPError as error:
        detail = f"Groq недоступний ({type(error).__name__}) — відповіді затримуватимуться"
        return CheckResult("Groq", False, detail, fatal=False)
    if response.status_code in (401, 403):
        detail = "GROQ_API_KEY недійсний — створи новий на https://console.groq.com/keys"
        return CheckResult("Groq", False, detail)
    if response.status_code != 200:
        detail = f"Groq відповів {response.status_code} — відповіді затримуватимуться"
        return CheckResult("Groq", False, detail, fatal=False)
    models = response.json().get("data", [])
    return CheckResult("Groq", True, f"ключ дійсний, моделей: {len(models)}")


async def _timed(check: Awaitable[CheckResult]) -> CheckResult:
    started = time.perf_counter()
    result = await check
    return CheckResult(
        result.name, result.ok, result.detail, result.fatal, time.perf_counter() - started
    )


def _log(result: CheckResult) -> None:
    if result.ok:
        logger.info("OK %s: %s (%.2f с)", result.name, result.detail, result.seconds)
    elif result.fatal:
        logger.error("ПОМИЛКА %s: %s", result.name, result.detail)
    else:
        logger.warning("УВАГА %s: %s", result.name, result.detail)


async def run_checks(
    settings: Settings, *, offline: bool = False, database: bool = True
) -> list[CheckResult]:
    """Запускає перевірки по черзі й логує кожну; повертає всі результати."""
    checks: list[Callable[[], Awaitable[CheckResult]]] = []
    if database:
        checks.append(lambda: check_database(settings))
    checks += [check_tesseract, check_antiword]
    if not offline:
        checks += [lambda: check_telegram(settings), lambda: check_groq(settings)]
    results = []
    for check in checks:
        result = await _timed(check())
        _log(result)
        results.append(result)
    return results


def exit_code(results: list[CheckResult], *, strict: bool = False) -> int:
    """0 — усе гаразд; 1 — критична помилка або (strict) хоч одне попередження."""
    failed = [r for r in results if not r.ok and (r.fatal or strict)]
    return 1 if failed else 0
