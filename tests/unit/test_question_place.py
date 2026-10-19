"""Місце в питанні (ADR-18, D-55): словник назв, описки, пастки, кілька місць, LLM."""

import json
from typing import Any

import httpx
import pytest

from forest_bot.llm.client import GroqClient
from forest_bot.places import PlaceGraph, load_places
from forest_bot.qa.places import detect_places, find_places, stem
from tests.factories import FAKE_GROQ_KEY


@pytest.fixture(scope="module")
def graph() -> PlaceGraph:
    return load_places()


@pytest.mark.parametrize(
    ("question", "place"),
    [
        # Офіційні назви у відмінках і з «у/в»
        ("Скільки внесок під дубом і до якого числа?", "pid-dubom"),
        ("Що можна їсти в ліщині?", "u-lishchyni"),
        ("Коли збори у Ліщині", "u-lishchyni"),
        ("Правила під ялинкою?", "pid-yalynoiu"),
        ("Хто чергує в соснах цього тижня", "u-sosnakh"),
        ("Небезпеки біля бобрової греблі", "bilia-bobrovoi-hrebli"),
        ("Правила рукавички «На узліссі»", "na-uzlissi"),
        ("Що їсти на узліссі", "na-uzlissi"),
        ("Як утеплити рукавичку біля річки?", "river"),
        ("Гриби в хвойному лісі", "coniferous"),
        ("Що їсти в листяному лісі?", "deciduous"),
        ("Що робити при пожежі у всьому лісі?", "forest"),
        ("Правила толоки для всього лісу", "forest"),
        # Побутові назви з places.yaml
        ("Що їсти біля греблі", "bilia-bobrovoi-hrebli"),
        ("Що є в листяній частині лісу", "deciduous"),
        ("прибережна частина: небезпеки", "river"),
        ("Що їсти над річкою", "river"),
        ("Внески в Дубовій рукавичці", "pid-dubom"),
        ("Скільки платять у Дубовій?", "pid-dubom"),
        ("Хто чергує в узліссі", "na-uzlissi"),
        # Описки
        ("внески під дубм", "pid-dubom"),
        ("що їсти в лішині", "u-lishchyni"),
        ("правила біля мурашніка", "bilia-murashnyka"),
    ],
)
def test_place_by_name(graph: PlaceGraph, question: str, place: str) -> None:
    found = find_places(question, graph)
    assert (found.place_ids, found.how) == ((place,), "name")


@pytest.mark.parametrize(
    "question",
    [
        # Прикметник-синонім не як назва рукавички
        "Чи можна їсти соснові шишки?",
        "Скільки дубового листя треба на підстилку?",
        "Чи смачна соснова кора?",
        # «У лісі» каже кожен — це не «весь ліс»
        "Що робити, якщо заблукав у лісі?",
        # Коротке слово-синонім лише цілим словом: «хвоя» ≠ «хвороба»
        "Чи небезпечна ця хвороба?",
        # «Біля верби» — не рукавичка «Під вербою»
        "Хто живе біля верби?",
        "Привіт!",
    ],
)
def test_no_place(graph: PlaceGraph, question: str) -> None:
    assert find_places(question, graph).place_ids == ()


def test_mitten_with_its_part_is_the_mitten(graph: PlaceGraph) -> None:
    assert find_places("Що їсти під вербою біля річки?", graph).place_ids == ("pid-verboiu",)


def test_part_and_whole_forest_is_the_part(graph: PlaceGraph) -> None:
    question = "Небезпеки листяного лісу і всього лісу"
    assert find_places(question, graph).place_ids == ("deciduous",)


def test_several_places_in_order(graph: PlaceGraph) -> None:
    # D-35: відповідь для кожного місця окремо
    found = find_places("Внески під дубом і в ліщині", graph)
    assert found.place_ids == ("pid-dubom", "u-lishchyni")


def test_rest_has_no_place_words(graph: PlaceGraph) -> None:
    found = find_places("Скільки внесок під дубом і до якого числа?", graph)
    assert found.rest == "Скільки внесок і до якого числа?"


@pytest.mark.parametrize(
    ("word", "base"),
    [("дубом", "дуб"), ("ліщині", "ліщин"), ("бобрової", "бобров"), ("ліс", "ліс")],
)
def test_stem(word: str, base: str) -> None:
    assert stem(word) == base


def groq(answer: dict[str, Any] | int, calls: list[int] | None = None) -> GroqClient:
    def handle(_: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(1)
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": {"message": "no"}})
        body = {
            "choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return httpx.Response(200, json=body)

    client = GroqClient(FAKE_GROQ_KEY, ("vendor/test",), transport=httpx.MockTransport(handle))
    client._max_wait = 0  # недоступна LLM — одразу кнопки, без очікування
    return client


@pytest.mark.parametrize(
    ("answer", "places", "how"),
    [
        ({"place_id": "pid-dubom", "confidence": 0.9}, ("pid-dubom",), "llm"),
        ({"place_id": "pid-dubom", "confidence": 0.5}, (), "none"),
        ({"place_id": "pid-klenom", "confidence": 0.99}, (), "none"),
        (503, (), "none"),
    ],
    ids=["confident", "unsure", "invented", "llm-down"],
)
async def test_llm_fallback(
    graph: PlaceGraph, answer: dict[str, Any] | int, places: tuple[str, ...], how: str
) -> None:
    llm = groq(answer)
    found = await detect_places("Що їсти коло старого дуба?", graph, llm)
    await llm.aclose()
    assert (found.place_ids, found.how) == (places, how)


async def test_name_found_without_llm(graph: PlaceGraph) -> None:
    calls: list[int] = []
    llm = groq({"place_id": "forest", "confidence": 1.0}, calls)
    found = await detect_places("Що їсти під дубом?", graph, llm)
    await llm.aclose()
    assert (found.place_ids, calls) == (("pid-dubom",), [])
