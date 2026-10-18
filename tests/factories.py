"""Фейкові, але валідні за формою токени й фабрика налаштувань."""

from pydantic import SecretStr

from forest_bot.config import Settings

FAKE_TELEGRAM_TOKEN = "123456789:" + "A" * 35
FAKE_GROQ_KEY = "gsk_" + "x" * 24


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": SecretStr(FAKE_TELEGRAM_TOKEN),
        "groq_api_key": SecretStr(FAKE_GROQ_KEY),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]
