"""БД для інтеграційних тестів: TEST_DATABASE_URL, схема на head, сесія з відкатом."""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import db
from forest_bot.config import Settings
from tests.factories import make_settings


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    # У CI тест без БД — помилка конфігурації, а не привід мовчки пропустити
    if os.environ.get("CI"):
        pytest.fail("TEST_DATABASE_URL не задано в CI")
    pytest.skip("TEST_DATABASE_URL не задано — інтеграційні тести потребують БД")


@pytest.fixture(scope="session")
def migrated(database_url: str) -> Settings:
    # Раз на прогін; синхронна, бо migrate сам запускає event loop
    settings = make_settings(database_url=database_url)
    assert db.migrate(settings) == 0
    return settings


@pytest.fixture
async def session(migrated: Settings) -> AsyncIterator[AsyncSession]:
    """Сесія всередині транзакції, яку після тесту відкочуємо: тести не бачать одне одного."""
    engine = db.create_engine(migrated)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def sessions(migrated: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Фабрика сесій для коду, що сам комітить (worker, імпорт): коміти — це savepoint-и
    всередині зовнішньої транзакції, яку після тесту відкочуємо."""
    engine = db.create_engine(migrated)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
        finally:
            await transaction.rollback()
    await engine.dispose()
