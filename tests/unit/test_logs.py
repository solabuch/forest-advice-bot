"""Логи: секрети не потрапляють у вивід ні в тексті, ні в JSON, ні в traceback."""

import io
import json
import logging

from forest_bot.logs import (
    MASK,
    TEXT_FORMAT,
    JsonFormatter,
    RedactingFormatter,
    context,
    log_context,
    redact,
)
from tests.factories import FAKE_GROQ_KEY, FAKE_TELEGRAM_TOKEN


def test_redact_by_pattern_and_value() -> None:
    text = (
        f"GET https://api.telegram.org/bot{FAKE_TELEGRAM_TOKEN}/getMe "
        f"key={FAKE_GROQ_KEY} invite=forest-secret"
    )
    out = redact(text, {"forest-secret"})
    assert FAKE_TELEGRAM_TOKEN not in out
    assert FAKE_GROQ_KEY not in out
    assert "forest-secret" not in out
    assert out.count(MASK) == 3


def _logger_with(formatter: logging.Formatter) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger(f"test.{type(formatter).__name__}")
    logger.handlers = [handler]
    logger.propagate = False
    return logger, stream


def test_formatter_redacts_args_and_traceback() -> None:
    logger, stream = _logger_with(RedactingFormatter("%(message)s"))
    try:
        raise RuntimeError(f"bad url .../bot{FAKE_TELEGRAM_TOKEN}/getMe")
    except RuntimeError:
        logger.exception("request failed: %s", FAKE_GROQ_KEY)
    out = stream.getvalue()
    assert FAKE_TELEGRAM_TOKEN not in out
    assert FAKE_GROQ_KEY not in out
    assert "RuntimeError" in out


def test_json_formatter() -> None:
    logger, stream = _logger_with(JsonFormatter())
    logger.warning("token %s", FAKE_TELEGRAM_TOKEN)
    record = json.loads(stream.getvalue())
    assert record["level"] == "WARNING"
    assert record["msg"] == f"token {MASK}"


def test_context_marks_records_and_is_reset() -> None:
    logger, stream = _logger_with(RedactingFormatter("%(name)s%(ctx)s: %(message)s"))
    with context("upd 812"):
        logger.warning("питання")
    logger.warning("поза апдейтом")
    assert stream.getvalue().splitlines() == [
        "test.RedactingFormatter [upd 812]: питання",
        "test.RedactingFormatter: поза апдейтом",
    ]
    assert log_context.get() == ""


def test_json_has_context() -> None:
    logger, stream = _logger_with(JsonFormatter(TEXT_FORMAT))
    with context("job 45 upload_index"):
        logger.warning("готово")
    assert json.loads(stream.getvalue())["ctx"] == "job 45 upload_index"
