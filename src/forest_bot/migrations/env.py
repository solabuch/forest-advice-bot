"""Alembic: міграції через async-рушій SQLAlchemy (asyncpg)."""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from forest_bot.config import load_database_settings
from forest_bot.models import Base

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    # forest_bot migrate передає URL явно; `alembic …` з консолі бере його з .env
    return config.get_main_option("sqlalchemy.url") or load_database_settings().database_url


def run_offline() -> None:
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
