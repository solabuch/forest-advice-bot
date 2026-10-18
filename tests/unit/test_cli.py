"""CLI: коди виходу й повідомлення для людини."""

import shutil

import pytest

from forest_bot import health
from forest_bot.__main__ import EXIT_CONFIG, main


def test_config_error_exits_2_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--offline", "--no-db"]) == EXIT_CONFIG
    out = capsys.readouterr().out
    assert "Не задано TELEGRAM_BOT_TOKEN" in out
    assert "Traceback" not in out


@pytest.mark.usefixtures("valid_env")
def test_offline_check_warnings_and_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert main(["check", "--offline", "--no-db"]) == 0
    assert main(["check", "--offline", "--no-db", "--strict"]) == 1


@pytest.mark.parametrize(("alive", "code"), [(True, 0), (False, 1)])
def test_health(monkeypatch: pytest.MonkeyPatch, alive: bool, code: int) -> None:
    monkeypatch.setattr(health, "is_alive", lambda: alive)
    assert main(["health"]) == code


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.startswith("forest_bot ")
