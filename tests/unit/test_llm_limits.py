"""Розбір заголовків лімітів Groq і стан моделі."""

import pytest

from forest_bot.llm.limits import DEFAULT_TPM, ModelState, parse_duration


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        ("1m26.4s", 86.4),
        ("7.66s", 7.66),
        ("2m59.56s", 179.56),
        ("120ms", 0.12),
        ("1h2m3s", 3723.0),
        ("2", 2.0),
        ("", None),
        (None, None),
        ("скоро", None),
    ],
)
def test_parse_duration(raw: str | None, seconds: float | None) -> None:
    result = parse_duration(raw)
    if seconds is None:
        assert result is None
    else:
        assert result == pytest.approx(seconds)


def test_fresh_model_is_available() -> None:
    state = ModelState()
    assert state.tpm_limit == DEFAULT_TPM
    assert state.available_at(5000, now=10.0) == 10.0


def test_headers_from_real_response() -> None:
    # Заголовки зі справжньої відповіді gpt-oss-120b, 18.10
    headers = {
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-requests": "999",
        "x-ratelimit-remaining-tokens": "7555",
        "x-ratelimit-reset-requests": "1m26.4s",
        "x-ratelimit-reset-tokens": "3.337s",
    }
    state = ModelState()
    state.update(headers, now=100.0)

    assert (state.tpm_limit, state.tokens_left, state.requests_left) == (8000, 7555, 999)
    assert state.available_at(7000, now=100.0) == 100.0
    # Запит на 7600 токенів не влізе — чекати, доки TPM оновиться
    assert state.available_at(7600, now=100.0) == pytest.approx(103.337)


def test_reserve_counts_parallel_requests() -> None:
    state = ModelState()
    state.update({"x-ratelimit-remaining-tokens": "3000", "x-ratelimit-reset-tokens": "5s"}, 0.0)
    state.reserve(2000)
    assert state.available_at(2000, now=1.0) == 5.0


def test_daily_requests_exhausted() -> None:
    state = ModelState()
    state.update(
        {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m"}, now=0.0
    )
    assert state.available_at(10, now=1.0) == 120.0


def test_block_keeps_latest_deadline() -> None:
    state = ModelState()
    state.block(50.0, "429")
    state.block(20.0, "503")
    assert state.available_at(10, now=0.0) == 50.0
