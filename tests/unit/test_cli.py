"""CLI: коди виходу й повідомлення для людини."""

import shutil
import subprocess
import sys

import pytest

from forest_bot import db, health, startup
from forest_bot.__main__ import EXIT_CONFIG, main
from forest_bot.config import DatabaseSettings, Settings
from forest_bot.startup import CheckResult


async def embeddings_ok(_: Settings) -> CheckResult:
    return CheckResult("Embeddings", True, "фейкова модель")


def test_config_error_exits_2_without_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--offline", "--no-db"]) == EXIT_CONFIG
    out = capsys.readouterr().out
    assert "Не задано TELEGRAM_BOT_TOKEN" in out
    assert "Traceback" not in out


@pytest.mark.usefixtures("valid_env")
def test_offline_check_warnings_and_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(startup, "check_embeddings", embeddings_ok)
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


def test_migrate_does_not_need_bot_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[DatabaseSettings] = []

    def fake_migrate(settings: DatabaseSettings) -> int:
        called.append(settings)
        return 0

    monkeypatch.setattr(db, "migrate", fake_migrate)
    assert main(["migrate"]) == 0
    assert len(called) == 1


def test_health_command_stays_light() -> None:
    # Docker викликає health кожні 30 с; імпорт aiogram робив його довшим за таймаут
    code = (
        "import sys; from forest_bot.__main__ import main; main(['health']); "
        "print(sorted({'aiogram', 'pydantic', 'sqlalchemy', 'asyncio'} & set(sys.modules)))"
    )
    result = subprocess.run(  # noqa: S603 — власний інтерпретатор, сталий код
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
