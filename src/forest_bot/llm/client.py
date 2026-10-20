"""Клієнт Groq (OpenAI-сумісний API): ланцюжок моделей, ліміти, structured output.

Ліміт чи збій однієї моделі не зупиняє бота: запит іде наступній моделі ланцюжка.
Коли зайняті всі, клієнт чекає найближчої доступності, попереджає через `on_wait`
(бот каже звірові про затримку, R-20), а понад MAX_WAIT — LlmUnavailableError.
Остання модель довгого ланцюжка — найслабша, запасна: її беремо, лише коли кращі
не звільняться за RESERVE_WAIT (eval: дві з трьох неповних відповідей — від неї).
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from forest_bot.llm.limits import ModelState, parse_duration
from forest_bot.llm.profiles import DEFAULT_CHAIN, ModelProfile, profile_for

logger = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
# Скільки один виклик може чекати звільнення моделей (TPM оновлюється за хвилину)
MAX_WAIT = 45.0
# Скільки варто почекати кращу модель, перш ніж відповідати запасною
RESERVE_WAIT = 20.0
# Чекаємо довше — кажемо звірові про затримку (R-20)
NOTIFY_AFTER = 3.0
# Пауза для моделі після 5xx, таймауту чи збою мережі
FAILURE_COOLDOWN = 10.0
# 404: моделі немає в Groq — не чіпати годину
MISSING_COOLDOWN = 3600.0
CONCURRENCY = 4
CACHE_SIZE, CACHE_TTL = 256, 3600.0
# Українська в токенізаторах цих моделей — ≈ 2 символи на токен; оцінка з запасом
CHARS_PER_TOKEN = 2
MESSAGE_OVERHEAD = 8

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)

Message = dict[str, str]
OnWait = Callable[[float], Awaitable[None]]


class LlmError(Exception):
    """Groq не дав відповіді."""


class LlmAuthError(LlmError):
    """Ключ недійсний: перемикання моделей не допоможе."""


class LlmUnavailableError(LlmError):
    """Усі моделі зайняті довше, ніж можна чекати; `retry_in` — через скільки секунд."""

    def __init__(self, retry_in: float | None) -> None:
        when = f" — спробуй за {retry_in:.0f} с" if retry_in is not None else ""
        super().__init__(f"усі моделі Groq зайняті або недоступні{when}")
        self.retry_in = retry_in


class LlmOutputError(LlmError):
    """Жодна модель не дала відповіді за схемою."""


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    attempts: int
    finish_reason: str
    cached: bool = False


def estimate_tokens(messages: Sequence[Message]) -> int:
    """Оцінка токенів промпту без токенізатора; точні числа — в `usage` відповіді."""
    return sum(len(m["content"]) // CHARS_PER_TOKEN + MESSAGE_OVERHEAD for m in messages)


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema для strict-режиму: усі поля обов'язкові, зайвих полів немає."""

    def walk(node: object) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("default", None)
            if isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    result = schema.model_json_schema()
    walk(result)
    return result


class _Cache:
    """Однакові запити (той самий контекст і питання) не витрачають ліміти (D-23)."""

    def __init__(self, size: int, ttl: float, clock: Callable[[], float]) -> None:
        self._items: OrderedDict[str, tuple[float, Completion]] = OrderedDict()
        self._size, self._ttl, self._clock = size, ttl, clock

    def get(self, key: str) -> Completion | None:
        item = self._items.get(key)
        if item is None or self._clock() - item[0] > self._ttl:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return item[1]

    def put(self, key: str, value: Completion) -> None:
        self._items[key] = (self._clock(), value)
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)


class GroqClient:
    def __init__(
        self,
        api_key: str,
        models: Sequence[str] = DEFAULT_CHAIN,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_wait: float = MAX_WAIT,
        concurrency: int = CONCURRENCY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not models:
            raise ValueError("потрібна хоч одна модель")
        self.models = tuple(models)
        self._states = {model: ModelState() for model in self.models}
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._slots = asyncio.Semaphore(concurrency)
        self._cache = _Cache(CACHE_SIZE, CACHE_TTL, clock)
        self._max_wait, self._clock, self._sleep = max_wait, clock, sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int = 600,
        temperature: float = 0.2,
        on_wait: OnWait | None = None,
    ) -> Completion:
        """Текстова відповідь; `finish_reason == "length"` — обрізана за max_tokens."""
        return await self._run(messages, max_tokens, temperature, None, on_wait)

    async def complete_json[T: BaseModel](
        self,
        messages: Sequence[Message],
        schema: type[T],
        *,
        max_tokens: int = 300,
        temperature: float = 0.0,
        on_wait: OnWait | None = None,
    ) -> tuple[T, Completion]:
        """Відповідь за pydantic-схемою; невалідна в усіх моделей → LlmOutputError."""
        completion = await self._run(messages, max_tokens, temperature, schema, on_wait)
        return schema.model_validate_json(completion.text), completion

    async def _run(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        schema: type[BaseModel] | None,
        on_wait: OnWait | None,
    ) -> Completion:
        key = self._cache_key(messages, max_tokens, temperature, schema)
        if (hit := self._cache.get(key)) is not None:
            return replace(hit, cached=True, attempts=0, seconds=0.0)

        started = self._clock()
        attempts, warned = 0, False
        # Моделі, які в цьому виклику вже не допоможуть: невалідний JSON, 413
        exhausted: set[str] = set()
        async with self._slots:
            while True:
                wait_better: float | None = None
                for index, model in enumerate(self.models):
                    profile = profile_for(model)
                    need = self._need(messages, max_tokens, profile)
                    state = self._states[model]
                    if model in exhausted or need > state.tpm_limit:
                        continue
                    if state.available_at(need, self._clock()) > self._clock():
                        continue
                    if self._is_reserve(index):
                        wait_better = self._better_soon(messages, max_tokens, exhausted, started)
                        if wait_better is not None:
                            break
                    attempts += 1
                    state.reserve(need)
                    body = self._body(model, profile, messages, max_tokens, temperature, schema)
                    result = await self._attempt(model, body, schema, exhausted)
                    if result is not None:
                        completion = replace(
                            result, seconds=self._clock() - started, attempts=attempts
                        )
                        logger.debug(
                            "Groq %s: %d+%d токенів, %.2f с, спроб %d",
                            model,
                            completion.prompt_tokens,
                            completion.completion_tokens,
                            completion.seconds,
                            attempts,
                        )
                        self._cache.put(key, completion)
                        return completion

                wait = (
                    wait_better
                    if wait_better is not None
                    else self._nearest(messages, max_tokens, exhausted)
                )
                if wait is None:
                    if exhausted and schema is not None:
                        raise LlmOutputError("жодна модель не дала відповіді за схемою")
                    raise LlmError("запит завеликий для всіх моделей ланцюжка")
                if self._clock() - started + wait > self._max_wait:
                    raise LlmUnavailableError(wait)
                if on_wait is not None and not warned and wait > NOTIFY_AFTER:
                    warned = True
                    await on_wait(wait)
                logger.info("Усі моделі Groq зайняті, чекаю %.1f с", wait)
                await self._sleep(wait)

    async def _attempt(
        self,
        model: str,
        body: dict[str, Any],
        schema: type[BaseModel] | None,
        exhausted: set[str],
    ) -> Completion | None:
        """Один запит до однієї моделі; None — перейти до наступної."""
        state = self._states[model]
        try:
            response = await self._http.post(API_URL, json=body)
        except httpx.HTTPError as error:
            state.block(self._clock() + FAILURE_COOLDOWN, type(error).__name__)
            logger.warning("Groq %s: %s → наступна модель", model, type(error).__name__)
            return None
        now = self._clock()
        state.update(response.headers, now)
        status = response.status_code

        if status == httpx.codes.OK:
            return self._parse(model, response, schema, exhausted)
        if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise LlmAuthError("GROQ_API_KEY недійсний або без доступу")
        if status == httpx.codes.TOO_MANY_REQUESTS:
            retry = parse_duration(response.headers.get("retry-after"))
            state.block(now + (retry if retry is not None else FAILURE_COOLDOWN), "429")
        elif status == httpx.codes.NOT_FOUND:
            state.block(now + MISSING_COOLDOWN, "немає в Groq")
        elif status == httpx.codes.REQUEST_ENTITY_TOO_LARGE or (
            status == httpx.codes.BAD_REQUEST and schema is not None
        ):
            # Завеликий запит для моделі або Groq не зміг видати JSON за схемою
            exhausted.add(model)
        elif status >= httpx.codes.INTERNAL_SERVER_ERROR or status in (422, 498):
            state.block(now + FAILURE_COOLDOWN, str(status))
        else:
            raise LlmError(f"Groq {model}: {status} {_error_message(response)}")
        logger.warning("Groq %s: %s %s → наступна модель", model, status, _error_message(response))
        return None

    def _parse(
        self,
        model: str,
        response: httpx.Response,
        schema: type[BaseModel] | None,
        exhausted: set[str],
    ) -> Completion | None:
        try:
            data = response.json()
            choice = data["choices"][0]
            text = _THINK.sub("", choice["message"].get("content") or "")
            finish = str(choice.get("finish_reason") or "")
            usage = data.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            exhausted.add(model)
            logger.warning("Groq %s: незрозуміла відповідь → наступна модель", model)
            return None
        if schema is not None:
            try:
                if finish == "length":
                    raise ValueError("обрізано за max_tokens")
                schema.model_validate_json(text)
            except (ValueError, ValidationError) as error:
                exhausted.add(model)
                logger.warning("Groq %s: відповідь не за схемою (%s)", model, error)
                return None
        return Completion(
            text=text,
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            seconds=0.0,
            attempts=0,
            finish_reason=finish,
        )

    def _need(self, messages: Sequence[Message], max_tokens: int, profile: ModelProfile) -> int:
        """Скільки TPM зарезервує запит: промпт + max_completion_tokens."""
        return (
            estimate_tokens(messages)
            + profile.prompt_overhead
            + max_tokens
            + profile.reasoning_reserve
        )

    def _is_reserve(self, index: int) -> bool:
        """Остання модель ланцюжка з трьох і більше — запасна."""
        return len(self.models) > 2 and index == len(self.models) - 1

    def _better_soon(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        exhausted: set[str],
        started: float,
    ) -> float | None:
        """Через скільки звільниться краща за запасну модель — якщо скоро й укладаємось
        у MAX_WAIT; інакше None (відповідає запасна)."""
        wait = self._nearest(messages, max_tokens, exhausted | {self.models[-1]})
        if wait is None or wait > RESERVE_WAIT:
            return None
        if self._clock() - started + wait > self._max_wait:
            return None
        return wait

    def _nearest(
        self, messages: Sequence[Message], max_tokens: int, exhausted: set[str]
    ) -> float | None:
        """Через скільки секунд звільниться хоч одна придатна модель; None — жодна."""
        now = self._clock()
        times = []
        for model in self.models:
            need = self._need(messages, max_tokens, profile_for(model))
            state = self._states[model]
            if model not in exhausted and need <= state.tpm_limit:
                times.append(state.available_at(need, now) - now)
        return max(min(times), 0.0) if times else None

    def _body(
        self,
        model: str,
        profile: ModelProfile,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        schema: type[BaseModel] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "max_completion_tokens": max_tokens + profile.reasoning_reserve,
            "temperature": temperature,
            **profile.params,
        }
        if schema is not None:
            json_schema = strict_json_schema(schema)
            if profile.strict_json:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "strict": True, "schema": json_schema},
                }
            else:
                hint = "Відповідай лише JSON за схемою: " + json.dumps(
                    json_schema, ensure_ascii=False
                )
                body["messages"] = [*messages, {"role": "system", "content": hint}]
                body["response_format"] = {"type": "json_object"}
        return body

    def _cache_key(
        self,
        messages: Sequence[Message],
        max_tokens: int,
        temperature: float,
        schema: type[BaseModel] | None,
    ) -> str:
        payload = [
            self.models,
            list(messages),
            max_tokens,
            temperature,
            schema.model_json_schema() if schema else None,
        ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return response.text[:200]
