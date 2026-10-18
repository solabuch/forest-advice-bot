"""Місце документа (ADR-16): фраза адресата → LLM → перепитати автора.

Документ стосується рівно одного місця (R-07), вгадувати не можна (R-08).
В архіві адресат — у першому абзаці: «Ця пам'ятка для мешканців біля річки»,
«Шановні мешканці рукавички «Під дубом»», «Любі сусіди з листяного лісу!».
Береться перше речення, де згадано місце; назви місць далі в тексті — не рішення:
«серед однакових сосен» ≠ «У соснах», «біля верби» ≠ «Під вербою».
"""

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Literal

from forest_bot.ingest.parse import ParsedDocument
from forest_bot.llm.client import GroqClient, LlmError
from forest_bot.llm.prompts import PlaceGuess, place_messages, resolve_place
from forest_bot.places import PlaceGraph, PlaceKind, normalize_name

logger = logging.getLogger(__name__)

# Скільки тексту з початку документа дивимось: адресат — у першому абзаці
OPENING_CHARS = 800
LLM_CHARS = 1500
LLM_MIN_CONFIDENCE = 0.8
# Назва в лапках, розпізнана OCR з помилкою («У еоснах»), — те саме місце
QUOTED_SIMILARITY = 0.8

_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_QUOTED = re.compile(r"«([^»]{2,40})»")
# Частини — за основою в будь-якому відмінку: «хвойного лісу», «у листяному лісі»
_PARTS = {
    "coniferous": re.compile(r"\bхвойн\w*", re.IGNORECASE),
    "deciduous": re.compile(r"\bлистян\w*", re.IGNORECASE),
    "river": re.compile(
        r"\b(?:біля|коло|над|при)\s+річк\w*|\bприбереж\w*|\bрічков\w*", re.IGNORECASE
    ),
}
# Увесь ліс: «мешканців лісу», «звірям нашого лісу», «всього лісу» — без прикметника частини
_FOREST = re.compile(
    r"\b(?:мешканц\w*|звір\w*|сусід\w*)\s+(?:(?:нашого|цілого|всього|усього)\s+)?лісу\b"
    r"|\b(?:всього|усього|весь|увесь|цілий)\s+ліс\w*",
    re.IGNORECASE,
)

How = Literal["phrase", "llm", "ask"]


@dataclass(frozen=True)
class PlaceDecision:
    """Місце документа; `place_id` None → питаємо автора кнопками."""

    place_id: str | None
    how: How
    # Кілька різних місць в одній фразі адресата — просимо розділити документ (R-16)
    several: tuple[str, ...] = ()
    confidence: float = 1.0


def _mitten_by_name(name: str, graph: PlaceGraph) -> str | None:
    key = normalize_name(name)
    best, score = None, 0.0
    for mitten in graph.of_kind(PlaceKind.MITTEN):
        ratio = difflib.SequenceMatcher(None, key, normalize_name(mitten.name)).ratio()
        if ratio > score:
            best, score = mitten.id, ratio
    return best if score >= QUOTED_SIMILARITY else None


def places_in(sentence: str, graph: PlaceGraph) -> list[str]:
    """Місця, згадані в реченні, у порядку появи; без повторів."""
    found: list[tuple[int, str]] = []
    for match in _QUOTED.finditer(sentence):
        if mitten := _mitten_by_name(match.group(1), graph):
            found.append((match.start(), mitten))
    for place_id, pattern in _PARTS.items():
        found.extend((m.start(), place_id) for m in pattern.finditer(sentence))
    found.extend((m.start(), graph.forest.id) for m in _FOREST.finditer(sentence))
    ordered: list[str] = []
    for _, place_id in sorted(found):
        if place_id not in ordered:
            ordered.append(place_id)
    return ordered


def _most_specific(places: list[str], graph: PlaceGraph) -> str | None:
    """Рукавичка + її частина («рукавичка «Під дубом» у листяному лісі») — це рукавичка.

    Інші поєднання (дві рукавички, дві частини, ліс і частина) — кілька місць.
    """
    if len(places) == 1:
        return places[0]
    mittens = [p for p in places if graph.get(p).kind is PlaceKind.MITTEN]
    if len(mittens) == 1:
        own_parts = set(graph.get(mittens[0]).parts)
        if all(p == mittens[0] or p in own_parts for p in places):
            return mittens[0]
    return None


def addressee(text: str, graph: PlaceGraph) -> tuple[str | None, tuple[str, ...]]:
    """Місце з першого речення, де воно згадане: (місце, ()) або (None, кілька місць)."""
    for sentence in _SENTENCE.split(text):
        places = places_in(sentence, graph)
        if places:
            place = _most_specific(places, graph)
            return (place, ()) if place else (None, tuple(places))
    return None, ()


def _opening(document: ParsedDocument, limit: int) -> str:
    """Перші абзаци без заголовків: саме там адресат."""
    parts, size = [], 0
    for block in document.blocks:
        if block.heading:
            continue
        parts.append(block.text)
        size += len(block.text)
        if size >= limit:
            break
    return "\n".join(parts)[:limit]


async def detect_place(
    document: ParsedDocument, graph: PlaceGraph, llm: GroqClient | None = None
) -> PlaceDecision:
    """Фраза адресата в першому абзаці → у назві → LLM → питаємо автора."""
    for text in (_opening(document, OPENING_CHARS), document.title or ""):
        place, several = addressee(text, graph)
        if place:
            return PlaceDecision(place, "phrase")
        if several:
            return PlaceDecision(None, "ask", several=several)
    if llm is None:
        return PlaceDecision(None, "ask", confidence=0.0)

    text = "\n".join(filter(None, [document.title, _opening(document, LLM_CHARS)]))
    try:
        guess, _ = await llm.complete_json(
            place_messages(text, graph, source="документ"), PlaceGuess, max_tokens=60
        )
    except LlmError as error:
        logger.warning("Місце документа: LLM недоступна (%s) — питаю автора", error)
        return PlaceDecision(None, "ask", confidence=0.0)
    guess = resolve_place(guess, graph)
    if guess.place_id and guess.confidence >= LLM_MIN_CONFIDENCE:
        return PlaceDecision(guess.place_id, "llm", confidence=guess.confidence)
    return PlaceDecision(None, "ask", confidence=guess.confidence)
