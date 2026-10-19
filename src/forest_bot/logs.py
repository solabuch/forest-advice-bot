"""Логування: усе в stdout, секрети маскуються в кожному записі.

Кожен запис несе контекст — апдейт Telegram (`upd 812`) чи задачу черги (`job 45 upload_index`):
так з потоку логів збирається один сценарій, навіть коли звірів багато. Хто саме писав і що,
у лог не потрапляє — лише номер апдейту.
"""

import io
import json
import logging
import logging.config
import re
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import ClassVar

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s%(ctx)s: %(message)s"

log_context: ContextVar[str] = ContextVar("log_context", default="")


@contextmanager
def context(label: str) -> Iterator[None]:
    """Усі записи всередині — з цією позначкою (і в дочірніх задачах asyncio)."""
    token = log_context.set(label)
    try:
        yield
    finally:
        log_context.reset(token)


# Токен бота стоїть в URL Bot API, ключ Groq — у заголовках; ловимо за формою.
# Без \b: в URL токен іде впритул після «bot» (…/bot123:AA…), межі слова там немає
SECRET_PATTERNS = (
    re.compile(r"(?<!\d)\d{5,}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"gsk_[A-Za-z0-9]{8,}"),
)
MASK = "***"


class RedactingFormatter(logging.Formatter):
    """Маскує секрети вже у відформатованому рядку — разом з args і traceback."""

    secrets: ClassVar[set[str]] = set()

    def format(self, record: logging.LogRecord) -> str:
        # Обробник пише синхронно, у тій самій задачі asyncio — контекст ще той самий
        label = log_context.get()
        record.ctx = f" [{label}]" if label else ""
        return redact(super().format(record), self.secrets)


class JsonFormatter(RedactingFormatter):
    """Один JSON-об'єкт на рядок; повний формат — разом з observability."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if label := log_context.get():
            payload["ctx"] = label
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False), self.secrets)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Замінює відомі значення секретів і все, що схоже на токен, на ***."""
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, MASK)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


def setup_logging(level: str = "INFO", fmt: str = "text", secrets: Iterable[str] = ()) -> None:
    """Налаштовує кореневий логер; чужі логери не вимикає."""
    RedactingFormatter.secrets = {s for s in secrets if s}
    # Windows у пайпі віддає stdout у cp1251 — кирилиця в логах ламається
    if isinstance(sys.stdout, io.TextIOWrapper) and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    formatter = JsonFormatter if fmt == "json" else RedactingFormatter
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"main": {"()": formatter, "format": TEXT_FORMAT}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "main",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {"level": level.upper(), "handlers": ["stdout"]},
            # httpx логує кожен запит на INFO — шум
            "loggers": {"httpx": {"level": "WARNING"}},
        }
    )
