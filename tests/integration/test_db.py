"""Інтеграція з БД: Postgres доступний, розширення pgvector встановлюється."""

import os

import asyncpg
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    # У CI тест без БД — помилка конфігурації, а не привід мовчки пропустити
    if os.environ.get("CI"):
        pytest.fail("TEST_DATABASE_URL не задано в CI")
    pytest.skip("TEST_DATABASE_URL не задано — інтеграційні тести потребують БД")


async def test_pgvector_extension_available(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        version = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
    finally:
        await conn.close()
    assert version
