"""Міграції БД: застосувати й перевірити, що схема на останній ревізії."""

import asyncio
import logging

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from forest_bot.config import DatabaseSettings

logger = logging.getLogger(__name__)

# Міграції лежать у пакеті — образу не потрібні alembic.ini і сирці
SCRIPT_LOCATION = "forest_bot:migrations"
CONNECT_ERRORS = (OSError, asyncpg.PostgresError, TimeoutError)
DB_WAIT_SECONDS = 60


def alembic_config(settings: DatabaseSettings) -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    # configparser сприймає «%» як підстановку — у паролі його треба подвоїти
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def head_revision(settings: DatabaseSettings) -> str | None:
    return ScriptDirectory.from_config(alembic_config(settings)).get_current_head()


async def current_revision(settings: DatabaseSettings) -> str | None:
    """Ревізія в БД; None — міграцій ще не було."""
    conn = await asyncpg.connect(settings.asyncpg_dsn, timeout=10)
    try:
        revision: str | None = await conn.fetchval("SELECT version_num FROM alembic_version")
    except asyncpg.UndefinedTableError:
        return None
    finally:
        await conn.close()
    return revision


async def wait_for_database(settings: DatabaseSettings) -> None:
    """Повторює підключення, доки БД не відповість; межу часу ставить викликач."""
    while True:
        try:
            conn = await asyncpg.connect(settings.asyncpg_dsn, timeout=5)
        except CONNECT_ERRORS:
            await asyncio.sleep(1)
        else:
            await conn.close()
            return


async def _database_ready(settings: DatabaseSettings) -> bool:
    try:
        async with asyncio.timeout(DB_WAIT_SECONDS):
            await wait_for_database(settings)
    except TimeoutError:
        return False
    return True


def migrate(settings: DatabaseSettings) -> int:
    """Чекає БД і застосовує міграції до head. Повертає код виходу."""
    if not asyncio.run(_database_ready(settings)):
        logger.error("БД недоступна %d с — міграції не застосовано", DB_WAIT_SECONDS)
        return 1
    before = asyncio.run(current_revision(settings))
    try:
        command.upgrade(alembic_config(settings), "head")
    except Exception:
        logger.exception("Міграція не вдалася — БД лишилася на ревізії %s", before)
        return 1
    after = asyncio.run(current_revision(settings))
    logger.info("Міграції: %s → %s", before or "порожня БД", after)
    return 0
