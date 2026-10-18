"""Міграції проти справжнього Postgres: up → down → up, моделі = міграції."""

import asyncio

import pytest
from alembic import command

from forest_bot import db
from forest_bot.config import Settings
from forest_bot.startup import check_migrations
from tests.factories import make_settings

pytestmark = pytest.mark.integration


def test_upgrade_downgrade_roundtrip(database_url: str) -> None:
    settings = make_settings(database_url=database_url)
    assert db.migrate(settings) == 0
    assert asyncio.run(db.current_revision(settings)) == db.head_revision(settings)

    command.downgrade(db.alembic_config(settings), "base")
    assert asyncio.run(db.current_revision(settings)) is None
    assert not asyncio.run(check_migrations(settings)).ok

    assert db.migrate(settings) == 0
    assert asyncio.run(check_migrations(settings)).ok


def test_models_match_migrations(migrated: Settings) -> None:
    # Падає, якщо моделі змінили, а міграцію не згенерували
    command.check(db.alembic_config(migrated))
