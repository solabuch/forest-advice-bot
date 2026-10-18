"""Конфіг: дефолти, зрозумілі помилки, розбір значень."""

import pytest

from forest_bot.config import ConfigError, load_settings
from tests.factories import FAKE_GROQ_KEY, FAKE_TELEGRAM_TOKEN, make_settings


def problems(monkeypatch: pytest.MonkeyPatch, **env: str) -> list[str]:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError) as caught:
        load_settings(env_file=None)
    return caught.value.problems


@pytest.mark.usefixtures("valid_env")
def test_defaults() -> None:
    settings = load_settings(env_file=None)
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.bot_timezone == "Europe/Kyiv"
    assert settings.role == "all"
    assert settings.invite_code is None
    assert settings.groq_models == []


def test_missing_tokens_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    found = problems(monkeypatch)
    assert len(found) == 2
    assert found[0].startswith("Не задано TELEGRAM_BOT_TOKEN — візьми токен у @BotFather")
    assert found[1].startswith("Не задано GROQ_API_KEY — створи ключ")


def test_empty_value_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    found = problems(monkeypatch, TELEGRAM_BOT_TOKEN="  ", GROQ_API_KEY=FAKE_GROQ_KEY)
    assert found == [
        "Не задано TELEGRAM_BOT_TOKEN — візьми токен у @BotFather (/newbot) і додай у .env "
        "(зразок — .env.example)"
    ]


def test_bad_formats_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    found = problems(monkeypatch, TELEGRAM_BOT_TOKEN="bad", GROQ_API_KEY="bad")
    assert found == [
        "TELEGRAM_BOT_TOKEN: має вигляд 123456789:AA… — перевір, чи скопійовано повністю",
        "GROQ_API_KEY: має починатися з gsk_",
    ]


@pytest.mark.usefixtures("valid_env")
def test_unknown_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    found = problems(monkeypatch, BOT_TIMEZONE="Mars/Olympus")
    assert found == ["BOT_TIMEZONE: невідомий часовий пояс, приклад — Europe/Kyiv"]


@pytest.mark.usefixtures("valid_env")
def test_values_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_MODELS", " model-a, model-b,,")
    monkeypatch.setenv("INVITE_CODE", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", f" {FAKE_TELEGRAM_TOKEN} ")
    settings = load_settings(env_file=None)
    assert settings.groq_models == ["model-a", "model-b"]
    assert settings.invite_code is None
    assert settings.telegram_bot_token.get_secret_value() == FAKE_TELEGRAM_TOKEN


def test_reads_env_file(tmp_path_factory: pytest.TempPathFactory) -> None:
    env_file = tmp_path_factory.mktemp("env") / ".env"
    env_file.write_text(
        f"TELEGRAM_BOT_TOKEN={FAKE_TELEGRAM_TOKEN}\nGROQ_API_KEY={FAKE_GROQ_KEY}\nLOG_LEVEL=DEBUG\n",
        encoding="utf-8",
    )
    assert load_settings(env_file=env_file).log_level == "DEBUG"


def test_asyncpg_dsn_and_secrets() -> None:
    settings = make_settings(database_url="postgresql+asyncpg://u:p@db:5432/forest")
    assert settings.asyncpg_dsn == "postgresql://u:p@db:5432/forest"
    assert settings.secret_values() == [FAKE_TELEGRAM_TOKEN, FAKE_GROQ_KEY]
    assert FAKE_TELEGRAM_TOKEN not in repr(settings)
