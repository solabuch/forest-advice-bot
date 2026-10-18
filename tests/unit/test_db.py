"""Міграції: конфіг alembic і ланцюжок ревізій без БД."""

from alembic.script import ScriptDirectory

from forest_bot import db
from tests.factories import make_settings


def test_alembic_config_escapes_percent() -> None:
    settings = make_settings(database_url="postgresql://forest:p%40ss@db:5432/forest")
    config = db.alembic_config(settings)
    assert config.get_main_option("script_location") == "forest_bot:migrations"
    assert config.get_main_option("sqlalchemy.url") == (
        "postgresql+asyncpg://forest:p%40ss@db:5432/forest"
    )


def test_single_head() -> None:
    # Дві гілки міграцій (два head) зламали б `upgrade head` у рев'юера
    script = ScriptDirectory.from_config(db.alembic_config(make_settings()))
    assert len(script.get_heads()) == 1
