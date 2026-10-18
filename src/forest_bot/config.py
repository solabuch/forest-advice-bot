"""Конфіг застосунку зі змінних оточення та `.env`.

Помилки конфігурації перетворюються на зрозумілі речення — без traceback.
"""

import re
from pathlib import Path
from typing import Annotated, Literal, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Тека «Лісового архіву» від Мишки (R-19)
DEFAULT_ARCHIVE_FOLDER_ID = "1b_hEjnkQfP8ZVP-OEbnYWqoGSFwXMAP3"

TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

# Що робити, якщо обов'язкової змінної немає
MISSING_HINTS = {
    "TELEGRAM_BOT_TOKEN": "візьми токен у @BotFather (/newbot) і додай у .env",
    "GROQ_API_KEY": "створи ключ на https://console.groq.com/keys і додай у .env",
}


class ConfigError(Exception):
    """Конфігурація невалідна; `problems` — по одному реченню на змінну."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


class DatabaseSettings(BaseSettings):
    """БД і логи — усе, що потрібно міграціям; токени бота їм не потрібні."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    database_url: str = "postgresql+asyncpg://forest:forest@db:5432/forest"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        # Звичний postgresql://… → драйвер asyncpg для SQLAlchemy
        if not value.startswith(("postgresql://", "postgresql+asyncpg://")):
            raise ValueError("очікується postgresql://… або postgresql+asyncpg://…")
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)

    @property
    def asyncpg_dsn(self) -> str:
        """DSN для asyncpg: без драйвера SQLAlchemy у схемі."""
        return re.sub(r"^postgresql\+\w+://", "postgresql://", self.database_url)

    def secret_values(self) -> list[str]:
        """Значення, які треба маскувати в логах."""
        return []


class Settings(DatabaseSettings):
    """Усі налаштування застосунку; обов'язкові лише два токени."""

    telegram_bot_token: SecretStr
    groq_api_key: SecretStr
    bot_timezone: str = "Europe/Kyiv"
    role: Literal["all", "bot", "worker"] = "all"
    archive_folder_id: str = DEFAULT_ARCHIVE_FOLDER_ID
    archive_sync_interval: int = Field(default=900, ge=60)
    max_file_mb: int = Field(default=20, ge=1, le=2000)
    uploads_per_hour: int = Field(default=20, ge=1)
    invite_code: SecretStr | None = None
    groq_models: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("telegram_bot_token", "groq_api_key", mode="before")
    @classmethod
    def _not_empty(cls, value: object) -> object:
        # `TOKEN=` у .env — те саме, що відсутній токен
        if isinstance(value, str) and not value.strip():
            raise ValueError("missing")
        return value

    @field_validator("telegram_bot_token")
    @classmethod
    def _telegram_token_format(cls, value: SecretStr) -> SecretStr:
        if not TELEGRAM_TOKEN_RE.match(value.get_secret_value().strip()):
            raise ValueError("має вигляд 123456789:AA… — перевір, чи скопійовано повністю")
        return SecretStr(value.get_secret_value().strip())

    @field_validator("groq_api_key")
    @classmethod
    def _groq_key_format(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip().startswith("gsk_"):
            raise ValueError("має починатися з gsk_")
        return SecretStr(value.get_secret_value().strip())

    @field_validator("bot_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("невідомий часовий пояс, приклад — Europe/Kyiv") from None
        return value

    @field_validator("invite_code", mode="before")
    @classmethod
    def _empty_invite_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("groq_models", mode="before")
    @classmethod
    def _split_models(cls, value: object) -> object:
        # GROQ_MODELS=model-a,model-b → ["model-a", "model-b"]
        if isinstance(value, str):
            return [m.strip() for m in value.split(",") if m.strip()]
        return value

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.bot_timezone)

    def secret_values(self) -> list[str]:
        """Значення, які треба маскувати в логах."""
        secrets = [self.telegram_bot_token, self.groq_api_key, self.invite_code]
        return [s.get_secret_value() for s in secrets if s is not None]


def describe_errors(error: ValidationError) -> list[str]:
    """Перекладає помилки pydantic у речення для людини."""
    problems = []
    for item in error.errors():
        name = str(item["loc"][0]).upper() if item["loc"] else "?"
        message = str(item.get("ctx", {}).get("error", item["msg"]))
        if item["type"] == "missing" or message == "missing":
            hint = MISSING_HINTS.get(name, "додай у .env")
            problems.append(f"Не задано {name} — {hint} (зразок — .env.example)")
        else:
            problems.append(f"{name}: {message}")
    return problems


SettingsT = TypeVar("SettingsT", bound=DatabaseSettings)


def _load[SettingsT: DatabaseSettings](model: type[SettingsT], env_file: Path | None) -> SettingsT:
    try:
        return model(_env_file=env_file)
    except ValidationError as error:
        raise ConfigError(describe_errors(error)) from None


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Повний конфіг; невалідний → ConfigError зі зрозумілими реченнями."""
    return _load(Settings, env_file)


def load_database_settings(env_file: Path | None = Path(".env")) -> DatabaseSettings:
    """Лише БД і логи — для міграцій."""
    return _load(DatabaseSettings, env_file)
