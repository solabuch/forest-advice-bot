"""Інтеграція з БД: Postgres доступний, розширення pgvector встановлюється."""

import asyncpg
import pytest

pytestmark = pytest.mark.integration


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
