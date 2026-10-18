"""Спільні фікстури: чисте оточення й валідні фейкові токени."""

import logging
from collections.abc import Iterator

import pytest

from forest_bot.config import Settings
from tests.factories import FAKE_GROQ_KEY, FAKE_TELEGRAM_TOKEN


@pytest.fixture(autouse=True)
def clean_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    # Справжні змінні й .env розробника не впливають на тести
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    yield
    # setup_logging чіпляє обробник до потоку capsys — після тесту він закритий
    logging.getLogger().handlers.clear()


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TELEGRAM_TOKEN)
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ_KEY)
