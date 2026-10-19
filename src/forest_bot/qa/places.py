"""Місце в питанні (ADR-18, D-55): словник назв із places.yaml → LLM → кнопки.

Звір називає місце сам (R-09), хто питає — не враховується (R-18), вгадувати не можна (D-06).
Словник будується з places.yaml: нова назва чи синонім — правка файлу, а не коду. Код знає
лише українську морфологію: закінчення, взаємозамінні «у/в», форми слова «ліс».
"""

import difflib
import logging
import re
from dataclasses import dataclass
from functools import cache
from typing import Literal

from forest_bot.llm.client import GroqClient, LlmError
from forest_bot.llm.prompts import PlaceGuess, place_messages, resolve_place
from forest_bot.places import Place, PlaceGraph, PlaceKind

logger = logging.getLogger(__name__)

LLM_MIN_CONFIDENCE = 0.8
# Описка в назві рукавички: «під дубм», «в лішині» — та сама рукавичка
TYPO_SIMILARITY = 0.8

# Закінчення, які відкидаємо, щоб ловити назву в будь-якому відмінку; довші — першими
_ENDINGS = sorted(
    {
        *("ами", "ями", "ові", "еві", "ого", "ому", "ими", "ій", "ої", "ою", "ею", "ом", "ем"),
        *("ах", "ях", "ів", "їв", "ей", "ий", "им", "ім", "их"),
        *("а", "я", "і", "ї", "и", "у", "ю", "е", "о"),
    },
    key=lambda ending: (-len(ending), ending),
)
_MIN_STEM = 3
_SWAPPABLE = ("у", "в", "ві", "уві")
_PREPOSITIONS = {*_SWAPPABLE, "під", "біля", "на", "над", "коло", "для", "при", "з", "із"}
# Прикметник-синонім рукавички («Дубова») — лише як назва: жіночий рід однини, а далі
# «рукавичка», кінець речення чи сполучник. Інакше «соснові шишки» стали б «У соснах»
_FEMININE = r"(?:а|ої|ій|у|ою)"
_AS_NAME = r"(?=\s+рукавич|\s*[?!.,;:)»\"]|\s*$|\s+(?:і|й|та|чи|або|ні)\b)"
# Увесь ліс — окремими формами: просто «у лісі» місцем не є, це може бути будь-яка частина
_FOREST = (
    r"(?:весь|увесь|цілий)\s+ліс"
    r"|(?:у|в)\s+(?:всьому|усьому|цілому)\s+лісі"
    r"|(?:всього|усього|цілого)\s+лісу"
    r"|(?:мешканц|звір)\w*\s+(?:нашого\s+)?лісу\b"
)
_QUOTED = re.compile(r"«([^»]{2,40})»")
_TOKEN = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*")

How = Literal["name", "llm", "none"]


@dataclass(frozen=True)
class QuestionPlaces:
    """Місця з питання у порядку згадки; порожньо → питаємо кнопками (D-06)."""

    place_ids: tuple[str, ...]
    how: How
    confidence: float = 1.0
    # Питання без назв місць — для пошуку за словами: «під дубом» не має шукати «дуб»
    rest: str = ""


def normalize(text: str) -> str:
    """Регістр і апострофи; довжина та сама — позиції збігів лишаються дійсними."""
    return text.casefold().replace("’", "'").replace("ʼ", "'").replace("`", "'")


def stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_STEM:
            return word[: -len(ending)]
    return word


def _word_pattern(word: str, *, in_phrase: bool) -> str:
    if word in _SWAPPABLE:
        return "(?:" + "|".join(_SWAPPABLE) + ")"
    if word in _PREPOSITIONS:
        return re.escape(word)
    base = stem(word)
    # Коротка основа окремого слова («хво» від «хвоя») зловила б «хвороба» — лише точно
    if len(base) < 4 and not in_phrase:
        return re.escape(word)
    return re.escape(base) + r"\w*"


def _name_pattern(name: str, place: Place) -> str:
    words = _TOKEN.findall(normalize(name))
    if not words:
        return ""
    if len(words) == 1 and place.kind is PlaceKind.MITTEN and words[0].endswith("а"):
        return re.escape(stem(words[0])) + _FEMININE + _AS_NAME
    parts = [_word_pattern(w, in_phrase=len(words) > 1) for w in words]
    return r"[\s«»\"']+".join(parts)


@dataclass(frozen=True)
class _Name:
    place_id: str
    pattern: re.Pattern[str]


@cache
def _gazetteer(graph: PlaceGraph) -> tuple[_Name, ...]:
    names: list[_Name] = []
    for place in graph.places:
        # «Ліс» одним словом — не назва: «у лісі» каже кожен, хто питає про будь-що
        variants = place.aliases if place.kind is PlaceKind.FOREST else (place.name, *place.aliases)
        for variant in variants:
            if pattern := _name_pattern(variant, place):
                names.append(_Name(place.id, re.compile(rf"(?<!\w){pattern}")))
    names.append(_Name(graph.forest.id, re.compile(rf"(?<!\w)(?:{_FOREST})")))
    return tuple(names)


def _mitten_by_name(name: str, graph: PlaceGraph) -> str | None:
    best, score = None, 0.0
    for mitten in graph.of_kind(PlaceKind.MITTEN):
        ratio = difflib.SequenceMatcher(None, normalize(name), normalize(mitten.name)).ratio()
        if ratio > score:
            best, score = mitten.id, ratio
    return best if score >= TYPO_SIMILARITY else None


def _typos(text: str, graph: PlaceGraph) -> list[tuple[int, int, str]]:
    """«під дубм», «в лішині»: прийменник з назви + схоже слово (чи слова) після нього."""
    tokens = list(_TOKEN.finditer(text))
    found = []
    for mitten in graph.of_kind(PlaceKind.MITTEN):
        words = _TOKEN.findall(normalize(mitten.name))
        if len(words) < 2 or words[0] not in _PREPOSITIONS:
            continue
        prepositions = _SWAPPABLE if words[0] in _SWAPPABLE else (words[0],)
        tail = " ".join(words[1:])
        for index, token in enumerate(tokens[: len(tokens) - len(words) + 1]):
            if token.group() not in prepositions:
                continue
            following = tokens[index + 1 : index + len(words)]
            candidate = " ".join(t.group() for t in following)
            if difflib.SequenceMatcher(None, candidate, tail).ratio() >= TYPO_SIMILARITY:
                found.append((token.start(), following[-1].end(), mitten.id))
    return found


def _reduce(place_ids: list[str], graph: PlaceGraph) -> list[str]:
    """«Під дубом у листяному лісі» — це рукавичка: частина й ліс уже в її області."""
    covered = {a.place.id for p in place_ids for a in graph.applicable(p) if a.place.id != p}
    return [p for p in place_ids if p not in covered]


def _strip(text: str, spans: list[tuple[int, int]]) -> str:
    out, position = [], 0
    for start, end in sorted(spans):
        if start >= position:
            out.append(text[position:start])
            position = end
    out.append(text[position:])
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def find_places(question: str, graph: PlaceGraph) -> QuestionPlaces:
    """Лише словник, без LLM: детерміновано й не витрачає лімітів Groq."""
    text = normalize(question)
    hits: list[tuple[int, int, str]] = []
    for name in _gazetteer(graph):
        hits.extend((m.start(), m.end(), name.place_id) for m in name.pattern.finditer(text))
    for match in _QUOTED.finditer(text):
        if mitten := _mitten_by_name(match.group(1), graph):
            hits.append((match.start(), match.end(), mitten))
    matched = {place_id for _, _, place_id in hits}
    hits.extend(hit for hit in _typos(text, graph) if hit[2] not in matched)

    ordered: list[str] = []
    for _, _, place_id in sorted(hits):
        if place_id not in ordered:
            ordered.append(place_id)
    rest = _strip(question, [(start, end) for start, end, _ in hits])
    places = tuple(_reduce(ordered, graph))
    return QuestionPlaces(places, "name" if places else "none", 1.0 if places else 0.0, rest)


async def detect_places(question: str, graph: PlaceGraph, llm: GroqClient | None) -> QuestionPlaces:
    """Словник → LLM (лише id зі списку, поріг 0,8) → порожньо, тобто кнопки (D-06)."""
    found = find_places(question, graph)
    if found.place_ids or llm is None:
        return found
    try:
        guess, _ = await llm.complete_json(
            place_messages(question, graph, source="питання"), PlaceGuess, max_tokens=60
        )
    except LlmError as error:
        logger.warning("Місце в питанні: LLM недоступна (%s) — питаю кнопками", error)
        return found
    guess = resolve_place(guess, graph)
    if guess.place_id and guess.confidence >= LLM_MIN_CONFIDENCE:
        return QuestionPlaces((guess.place_id,), "llm", guess.confidence, found.rest)
    return QuestionPlaces((), "none", guess.confidence, found.rest)
