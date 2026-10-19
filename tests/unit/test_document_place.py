"""Місце документа (ADR-16): фраза адресата, пастки архіву, кілька місць, запасний шлях LLM."""

import json
import os
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from forest_bot.ingest.parse import Block, ParsedDocument, parse
from forest_bot.ingest.place import addressee, detect_place
from forest_bot.llm.client import GroqClient
from forest_bot.places import PlaceGraph, load_places
from tests.factories import FAKE_GROQ_KEY

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "data" / "archive"


@pytest.fixture(scope="module")
def graph() -> PlaceGraph:
    return load_places()


def document(text: str, title: str | None = None) -> ParsedDocument:
    blocks = [Block(title, title, heading=True)] if title else []
    blocks += [Block(p) for p in text.split("\n\n")]
    return ParsedDocument(blocks=tuple(blocks), title=title)


@pytest.mark.parametrize(
    ("text", "place"),
    [
        ("Шановні мешканці рукавички «Під дубом»! Щоб наш дім був теплим…", "pid-dubom"),
        ("Ця пам'ятка для мешканців біля річки. Біля верби вода глибока.", "river"),
        ("Любі сусіди з листяного лісу! Цей путівник для вас.", "deciduous"),
        ("Ця порада для тих, чия рукавичка стоїть у хвойному лісі.", "coniferous"),
        ("Для всіх, хто живе біля річки. Вода дає життя.", "river"),
        ("Це звернення до всіх мешканців лісу, від пташки до звіра.", "forest"),
        # Пастки архіву: місце — адресат, а не будь-яка згадка
        ("Ця порада для мешканців хвойного лісу. Між соснами легко збитися.", "coniferous"),
        (
            "Ця пам'ятка для всіх мешканців лісу, незалежно від того, де лежить рукавичка. "
            "Усі збираються біля бобрової греблі.",
            "forest",
        ),
        (
            "Ця карта для всіх мешканців лісу. Частин три: хвойний ліс, листяний ліс і біля річки.",
            "forest",
        ),
        # OCR прочитав «с» як «е» — назва в лапках однаково впізнається
        ("Це оголошення стосується всіх мешканців рукавички «У еоснах».", "u-sosnakh"),
        # Рукавичка разом зі своєю частиною — це рукавичка
        ("Для мешканців рукавички «Під дубом» у листяному лісі.", "pid-dubom"),
    ],
)
def test_addressee_phrase(graph: PlaceGraph, text: str, place: str) -> None:
    assert addressee(text, graph) == (place, ())


@pytest.mark.parametrize(
    ("text", "several"),
    [
        ("Для мешканців рукавичок «Під дубом» і «У ліщині».", ("pid-dubom", "u-lishchyni")),
        ("Порада для хвойного й листяного лісу.", ("coniferous", "deciduous")),
        ("Для рукавички «Під ялиною» і всіх, хто біля річки.", ("pid-yalynoiu", "river")),
    ],
)
def test_several_places_ask_to_split(
    graph: PlaceGraph, text: str, several: tuple[str, ...]
) -> None:
    assert addressee(text, graph) == (None, several)


def test_no_place_mentioned(graph: PlaceGraph) -> None:
    assert addressee("Гриби шукайте після дощу. Невідомий гриб не беріть.", graph) == (None, ())


async def test_title_is_the_second_chance(graph: PlaceGraph) -> None:
    decision = await detect_place(
        document("Гриби шукайте після дощу.", title="Грибний путівник біля річки"), graph
    )
    assert (decision.place_id, decision.how) == ("river", "phrase")


async def test_several_places_are_not_resolved_by_llm(graph: PlaceGraph) -> None:
    decision = await detect_place(document("Для рукавичок «Під дубом» і «У ліщині»."), graph)
    assert decision.place_id is None
    assert decision.several == ("pid-dubom", "u-lishchyni")


def groq(answer: dict[str, Any] | int) -> GroqClient:
    def handle(_: httpx.Request) -> httpx.Response:
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": {"message": "no"}})
        body = {
            "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return httpx.Response(200, json=body)

    return GroqClient(FAKE_GROQ_KEY, ("vendor/test",), transport=httpx.MockTransport(handle))


@pytest.mark.parametrize(
    ("answer", "place", "how"),
    [
        ({"place_id": "u-lishchyni", "confidence": 0.93}, "u-lishchyni", "llm"),
        # Невпевнено або вигадане місце — питаємо автора
        ({"place_id": "u-lishchyni", "confidence": 0.5}, None, "ask"),
        ({"place_id": "pid-klenom", "confidence": 0.99}, None, "ask"),
        (503, None, "ask"),
    ],
    ids=["confident", "unsure", "invented", "llm-down"],
)
async def test_llm_fallback(
    graph: PlaceGraph, answer: dict[str, Any] | int, place: str | None, how: str
) -> None:
    llm = groq(answer)
    llm._max_wait = 0  # недоступна LLM — одразу питаємо автора, без очікування
    decision = await detect_place(document("Горіхи збирайте, коли самі падають."), graph, llm)
    await llm.aclose()
    assert (decision.place_id, decision.how) == (place, how)


async def test_without_llm_asks(graph: PlaceGraph) -> None:
    decision = await detect_place(document("Горіхи збирайте, коли самі падають."), graph)
    assert (decision.place_id, decision.how) == (None, "ask")


async def test_whole_archive_without_llm(graph: PlaceGraph) -> None:
    # Ціль ADR-16: 78/78 за фразою адресата, без жодного виклику LLM
    if shutil.which("tesseract") is None:
        if os.environ.get("CI"):
            pytest.fail("tesseract не встановлено в CI")
        pytest.skip("немає tesseract — скани не прочитати")
    truth = yaml.safe_load((ROOT / "eval" / "archive_places.yaml").read_text(encoding="utf-8"))
    misses = []
    for name, expected in truth.items():
        decision = await detect_place(parse((ARCHIVE / name).read_bytes(), name), graph)
        if (decision.place_id, decision.how) != (expected, "phrase"):
            misses.append(f"{name}: {decision.place_id}, треба {expected}")
    assert misses == []
    assert len(truth) == 78
