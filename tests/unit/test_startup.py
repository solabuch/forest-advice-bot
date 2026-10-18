"""Самоперевірка: результати й коди виходу без мережі та зовнішніх програм."""

import shutil

import httpx
import pytest

from forest_bot import startup
from forest_bot.startup import CheckResult, check_groq, check_tesseract, exit_code
from tests.factories import make_settings


def groq_returns(status: int, body: object = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(status, json=body or {}))


async def test_groq_ok() -> None:
    result = await check_groq(make_settings(), groq_returns(200, {"data": [{}, {}]}))
    assert result.ok
    assert result.detail == "ключ дійсний, моделей: 2"


async def test_groq_invalid_key_is_fatal() -> None:
    result = await check_groq(make_settings(), groq_returns(401))
    assert (result.ok, result.fatal) == (False, True)
    assert "GROQ_API_KEY недійсний" in result.detail


@pytest.mark.parametrize("transport", [groq_returns(503), None])
async def test_groq_unavailable_is_warning(transport: httpx.MockTransport | None) -> None:
    def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    result = await check_groq(make_settings(), transport or httpx.MockTransport(refuse))
    assert (result.ok, result.fatal) == (False, False)


async def test_tesseract_missing_is_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = await check_tesseract()
    assert (result.ok, result.fatal) == (False, False)


async def test_tesseract_language_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def only_eng(_: str) -> set[str]:
        return {"eng", "osd"}

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setattr(startup, "_tesseract_langs", only_eng)
    result = await check_tesseract()
    assert not result.ok
    assert result.detail == "немає мовних моделей tesseract: ukr"


def test_exit_code() -> None:
    ok = CheckResult("a", True, "")
    warning = CheckResult("b", False, "", fatal=False)
    fatal = CheckResult("c", False, "")
    assert exit_code([ok, warning]) == 0
    assert exit_code([ok, warning], strict=True) == 1
    assert exit_code([ok, fatal]) == 1
