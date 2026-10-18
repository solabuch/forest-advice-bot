"""Самоперевірка БД проти справжнього Postgres."""

import pytest

from forest_bot.startup import check_database
from tests.factories import make_settings

pytestmark = pytest.mark.integration


async def test_database_check_ok(database_url: str) -> None:
    result = await check_database(make_settings(database_url=database_url))
    assert result.ok
    assert result.detail.startswith("Postgres ")


async def test_database_unreachable_is_fatal() -> None:
    settings = make_settings(database_url="postgresql://u:p@127.0.0.1:1/none")
    result = await check_database(settings)
    assert (result.ok, result.fatal) == (False, True)
