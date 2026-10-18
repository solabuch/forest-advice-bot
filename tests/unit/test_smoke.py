"""Smoke-тести каркаса: пакет імпортується, точка входу відпрацьовує."""

import logging
import sys

import pytest

import forest_bot.__main__ as entry
from forest_bot import __version__
from forest_bot.logs import setup_logging


def test_main_logs_version(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # dictConfig замінив би обробник caplog — тут перевіряємо лише main
    monkeypatch.setattr(entry, "setup_logging", lambda: None)
    with caplog.at_level(logging.INFO):
        assert entry.main() == 0
    assert f"forest_bot {__version__}" in caplog.text


def test_setup_logging_writes_to_stdout() -> None:
    setup_logging("debug")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stdout for h in root.handlers
    )
