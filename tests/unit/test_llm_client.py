"""Клієнт Groq: перемикання моделей, ліміти, очікування, structured output, кеш."""

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from forest_bot.llm.client import (
    CACHE_TTL,
    MAX_WAIT,
    Completion,
    GroqClient,
    LlmAuthError,
    LlmError,
    LlmOutputError,
    LlmUnavailableError,
    estimate_tokens,
    strict_json_schema,
)
from tests.factories import FAKE_GROQ_KEY

BIG, QWEN, SMALL = "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"
MESSAGES = [{"role": "user", "content": "Коли під дубом збирати жолуді?"}]

Reply = httpx.Response | Exception
Handler = Callable[[dict[str, Any]], Reply]


class FakeClock:
    """Монотонний годинник і sleep, що лише пересуває час."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def ok(content: str = "Жолуді — у жовтні.", finish: str = "stop", **headers: str) -> httpx.Response:
    body = {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 70, "completion_tokens": 12},
    }
    return httpx.Response(200, json=body, headers=headers)


def error(status: int, message: str = "помилка", **headers: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message}}, headers=headers)


class FakeGroq:
    """Відповіді за моделями по черзі; останню повторює. Запам'ятовує тіла запитів."""

    def __init__(self, replies: dict[str, list[Reply]]) -> None:
        self.replies = {model: list(items) for model, items in replies.items()}
        self.requests: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        queue = self.replies[body["model"]]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def models(self) -> list[str]:
        return [r["model"] for r in self.requests]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(clock: FakeClock) -> Iterator[Callable[..., tuple[GroqClient, FakeGroq]]]:
    def make(
        replies: dict[str, list[Reply]], models: tuple[str, ...] = (BIG, QWEN)
    ) -> tuple[GroqClient, FakeGroq]:
        fake = FakeGroq(replies)
        client = GroqClient(
            FAKE_GROQ_KEY,
            models,
            transport=httpx.MockTransport(fake.handle),
            clock=clock,
            sleep=clock.sleep,
        )
        return client, fake

    yield make


async def test_first_model_answers(make_client: Callable[..., tuple[GroqClient, FakeGroq]]) -> None:
    client, fake = make_client({BIG: [ok()], QWEN: [ok()]})
    completion = await client.complete(MESSAGES, max_tokens=200)

    assert (completion.text, completion.model, completion.attempts) == (
        "Жолуді — у жовтні.",
        BIG,
        1,
    )
    assert (completion.prompt_tokens, completion.completion_tokens) == (70, 12)
    body = fake.requests[0]
    # gpt-oss: міркування низькі й не повертаються, запас токенів на них — поверх відповіді
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False
    assert body["max_completion_tokens"] == 200 + 300


@pytest.mark.parametrize(
    "failure",
    [
        error(429, "rate limit", **{"retry-after": "7"}),
        error(503),
        error(500),
        httpx.ReadTimeout("read timeout"),
        httpx.ConnectError("no route"),
    ],
    ids=["429", "503", "500", "timeout", "network"],
)
async def test_failure_falls_back_to_next_model(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], failure: Reply
) -> None:
    client, fake = make_client({BIG: [failure], QWEN: [ok()]})
    completion = await client.complete(MESSAGES)

    assert completion.model == QWEN
    assert completion.attempts == 2
    assert fake.models == [BIG, QWEN]


async def test_rate_limited_model_is_skipped_until_retry_after(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], clock: FakeClock
) -> None:
    # D-23: ліміт однієї моделі → наступна в ланцюжку
    client, fake = make_client({BIG: [error(429, **{"retry-after": "30"}), ok()], QWEN: [ok()]})
    await client.complete(MESSAGES)
    await client.complete([{"role": "user", "content": "Інше питання"}])
    assert fake.models == [BIG, QWEN, QWEN]

    clock.now += 31
    completion = await client.complete([{"role": "user", "content": "Ще одне"}])
    assert completion.model == BIG


async def test_missing_model_is_skipped(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, fake = make_client({BIG: [error(404, "model_not_found")], QWEN: [ok()]})
    await client.complete(MESSAGES)
    await client.complete([{"role": "user", "content": "Інше питання"}])
    assert fake.models == [BIG, QWEN, QWEN]


async def test_invalid_key_stops_without_fallback(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, fake = make_client({BIG: [error(401)], QWEN: [ok()]})
    with pytest.raises(LlmAuthError):
        await client.complete(MESSAGES)
    assert fake.models == [BIG]


async def test_bad_request_is_not_retried(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, fake = make_client({BIG: [error(400, "bad field")], QWEN: [ok()]})
    with pytest.raises(LlmError, match="bad field"):
        await client.complete(MESSAGES)
    assert fake.models == [BIG]


async def test_waits_for_nearest_model(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], clock: FakeClock
) -> None:
    client, fake = make_client(
        {
            BIG: [error(429, **{"retry-after": "2"}), ok()],
            QWEN: [error(429, **{"retry-after": "5"})],
        }
    )
    warnings: list[float] = []

    async def on_wait(seconds: float) -> None:
        warnings.append(seconds)

    completion = await client.complete(MESSAGES, on_wait=on_wait)

    assert completion.model == BIG
    assert clock.slept == [2.0]
    # Коротке очікування звіра не турбує
    assert warnings == []
    assert fake.models == [BIG, QWEN, BIG]


async def test_long_wait_warns_once(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], clock: FakeClock
) -> None:
    # R-20, D-23: затримка через ліміти — попередження один раз
    client, _ = make_client(
        {
            BIG: [error(429, **{"retry-after": "12"}), ok()],
            QWEN: [error(429, **{"retry-after": "20"})],
        }
    )
    warnings: list[float] = []

    async def on_wait(seconds: float) -> None:
        warnings.append(seconds)

    await client.complete(MESSAGES, on_wait=on_wait)
    assert warnings == [12.0]


async def test_all_busy_beyond_max_wait_raises_with_retry_in(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], clock: FakeClock
) -> None:
    retry = str(int(MAX_WAIT) + 15)
    client, _ = make_client(
        {BIG: [error(429, **{"retry-after": retry})], QWEN: [error(429, **{"retry-after": retry})]}
    )
    with pytest.raises(LlmUnavailableError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.retry_in == float(retry)
    assert clock.slept == []


async def test_headers_prevent_request_that_would_hit_tpm(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    headers = {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "9.5s"}
    client, fake = make_client({BIG: [ok(**headers)], QWEN: [ok()]})
    await client.complete(MESSAGES)
    # У BIG лишилось 100 токенів TPM — менше, ніж зарезервує наступний запит
    await client.complete([{"role": "user", "content": "Інше питання"}])
    assert fake.models == [BIG, QWEN]


async def test_too_large_for_every_model(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, fake = make_client({BIG: [error(413)], QWEN: [error(413)]})
    with pytest.raises(LlmError, match="завеликий"):
        await client.complete(MESSAGES)
    assert fake.models == [BIG, QWEN]


async def test_identical_request_is_cached(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    # D-23: однакові питання — з кешу
    client, fake = make_client({BIG: [ok()], QWEN: [ok()]})
    first = await client.complete(MESSAGES)
    second = await client.complete(MESSAGES)

    assert len(fake.requests) == 1
    assert (first.cached, second.cached) == (False, True)
    assert second.text == first.text


async def test_think_block_is_stripped(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, _ = make_client({BIG: [ok("<think>міркую…</think>\nУ жовтні.")], QWEN: [ok()]})
    assert (await client.complete(MESSAGES)).text == "У жовтні."


class Guess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place_id: str | None
    confidence: float


async def test_json_uses_strict_schema(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, fake = make_client(
        {BIG: [ok('{"place_id": "pid-dubom", "confidence": 0.9}')], QWEN: [ok()]}
    )
    guess, completion = await client.complete_json(MESSAGES, Guess)

    assert guess == Guess(place_id="pid-dubom", confidence=0.9)
    assert isinstance(completion, Completion)
    response_format = fake.requests[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


@pytest.mark.parametrize(
    "bad",
    [
        ok("не JSON"),
        ok('{"place_id": "pid-dubom"}'),
        ok('{"place_id": "pid-du', finish="length"),
        error(400, "Generated JSON does not match the expected schema"),
    ],
    ids=["not-json", "schema", "truncated", "groq-400"],
)
async def test_invalid_json_falls_back(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], bad: httpx.Response
) -> None:
    client, fake = make_client({BIG: [bad], QWEN: [ok('{"place_id": null, "confidence": 0.2}')]})
    guess, completion = await client.complete_json(MESSAGES, Guess)
    assert (guess.place_id, completion.model) == (None, QWEN)
    assert fake.models == [BIG, QWEN]


async def test_no_valid_json_anywhere(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, _ = make_client({BIG: [ok("ні")], QWEN: [ok("теж ні")]})
    with pytest.raises(LlmOutputError):
        await client.complete_json(MESSAGES, Guess)


async def test_unknown_model_gets_json_object_mode(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, fake = make_client(
        {"vendor/new-model": [ok('{"place_id": "u-sosnakh", "confidence": 1}')]},
        models=("vendor/new-model",),
    )
    guess, _ = await client.complete_json(MESSAGES, Guess)

    assert guess.place_id == "u-sosnakh"
    body = fake.requests[0]
    assert body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body
    assert "JSON" in body["messages"][-1]["content"]


def test_strict_schema_requires_every_field() -> None:
    class Inner(BaseModel):
        model_config = ConfigDict(extra="forbid")
        note: str = ""

    class Outer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        inner: Inner
        score: float = 0.5

    schema = strict_json_schema(Outer)
    assert schema["required"] == ["inner", "score"]
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["required"] == ["note"]
    assert "title" not in schema
    assert "default" not in schema["properties"]["score"]


def test_estimate_is_conservative_for_ukrainian() -> None:
    # Справжній промпт зі спроби 18.10: qwen порахував 72 токени, gpt-oss — 178 з преамбулою
    messages = [
        {
            "role": "system",
            "content": "Визнач місце лісу з питання. Місця: pid-dubom (Під дубом), "
            "u-sosnakh (У соснах). Відповідь — JSON.",
        },
        {"role": "user", "content": "Коли під дубом збирати жолуді?"},
    ]
    assert estimate_tokens(messages) >= 72


def test_needs_a_model() -> None:
    with pytest.raises(ValueError, match="модель"):
        GroqClient(FAKE_GROQ_KEY, ())


async def test_garbled_answer_falls_back(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, fake = make_client({BIG: [httpx.Response(200, text="<html>")], QWEN: [ok()]})
    assert (await client.complete(MESSAGES)).model == QWEN
    assert fake.models == [BIG, QWEN]


async def test_plain_text_error_is_reported(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
) -> None:
    client, _ = make_client({BIG: [httpx.Response(418, text="teapot")], QWEN: [ok()]})
    with pytest.raises(LlmError, match="418 teapot"):
        await client.complete(MESSAGES)


async def test_cache_expires_and_evicts(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]],
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, fake = make_client({BIG: [ok()], QWEN: [ok()]})
    await client.complete(MESSAGES)
    clock.now += CACHE_TTL + 1
    assert not (await client.complete(MESSAGES)).cached

    monkeypatch.setattr(client._cache, "_size", 1)
    await client.complete([{"role": "user", "content": "Інше питання"}])
    assert not (await client.complete(MESSAGES)).cached
    assert len(fake.requests) == 4
    await client.aclose()


async def test_reserve_model_waits_for_a_better_one_freeing_soon(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], clock: FakeClock
) -> None:
    # Найслабша модель — запас: кращі звільняться за 8 с, тож чекаємо їх
    client, fake = make_client(
        {
            QWEN: [error(429, **{"retry-after": "8"}), ok()],
            BIG: [error(429, **{"retry-after": "15"})],
            SMALL: [ok("запасна")],
        },
        models=(QWEN, BIG, SMALL),
    )
    completion = await client.complete(MESSAGES)
    assert completion.model == QWEN
    assert clock.slept == [8.0]
    assert SMALL not in fake.models


async def test_reserve_model_answers_when_better_ones_are_far(
    make_client: Callable[..., tuple[GroqClient, FakeGroq]], clock: FakeClock
) -> None:
    client, _ = make_client(
        {
            QWEN: [error(429, **{"retry-after": "60"})],
            BIG: [error(429, **{"retry-after": "60"})],
            SMALL: [ok("запасна")],
        },
        models=(QWEN, BIG, SMALL),
    )
    completion = await client.complete(MESSAGES)
    assert (completion.model, clock.slept) == (SMALL, [])
