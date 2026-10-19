"""Текст відповіді: частини від конкретної поради до загальної, кожна зі своїм підписом.

Підписи збирає код, а не модель: назва, місце, автор і дата — з бази (R-12, D-18),
тож вигадати джерело неможливо (ADR-05).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from forest_bot.models import DocumentSource
from forest_bot.places import Place, PlaceGraph, PlaceKind
from forest_bot.qa.places import name_pattern
from forest_bot.qa.search import Passage

DIFFERS = "Поради в документах відрізняються — ось обидві, кожна зі своїм джерелом."
# Telegram не прийме довше; ділимо по абзацах
MESSAGE_LIMIT = 4096


@dataclass(frozen=True)
class RenderedPart:
    text: str
    passage: Passage


def short_title(title: str, place: Place) -> str:
    """Назва без хвоста з місцем — як в еталоні (§4): «Членські внески рукавички «Під дубом»»
    → «Членські внески», бо місце й так стоїть у підписі."""
    if place.kind is PlaceKind.FOREST:
        return title
    if place.kind is PlaceKind.MITTEN:
        name = re.escape(place.name)
        # Без лапок — лише після «рукавички»: «Ягоди на узліссі» лишається як є
        tail = (
            r"(?:\s+(?:(?:поруч|поблизу)\s+)?(?:(?:із|з|від|в|у|для|біля)\s+)?рукавич\w*"
            rf"\s+«?{name}»?|\s+«{name}»)"
        )
    else:
        tail = r"\s+(?:(?:в|у|для|з|із)\s+)?" + name_pattern(place.name, place)
    short = re.sub(rf"{tail}\s*$", "", title, flags=re.IGNORECASE).strip()
    return short if len(short) >= 4 else title


def _pages(pages: Sequence[int]) -> str:
    return f"с. {pages[0]}" if len(pages) == 1 else f"с. {pages[0]}–{pages[-1]}"


def signature(passage: Passage, graph: PlaceGraph, timezone: ZoneInfo) -> str:
    """`— з документа «Назва» рукавички «Місце»`; великий документ — ще розділ і сторінка;
    документ від звіра — ще `(надіслано: Ім'я, ДД.ММ.РРРР)`."""
    place = graph.get(passage.place_id)
    pieces = [f"— з документа «{short_title(passage.title, place)}» {place.citation}"]
    if not passage.whole:
        if passage.sections:
            pieces.append(f"розділ «{passage.sections[0]}»")
        if passage.pages:
            pieces.append(_pages(passage.pages))
    text = ", ".join(pieces)
    if passage.source is DocumentSource.TELEGRAM:
        sender = passage.sender or "звір"
        text += f" (надіслано: {sender}, {passage.sent_at.astimezone(timezone):%d.%m.%Y})"
    return text


def order(parts: Sequence[RenderedPart]) -> list[RenderedPart]:
    """Конкретна порада першою (R-11, R-24); у межах рівня — як дала модель."""
    return sorted(parts, key=lambda part: part.passage.level)


def compose(
    parts: Sequence[RenderedPart], graph: PlaceGraph, timezone: ZoneInfo, *, differs: bool
) -> str:
    ordered = order(parts)
    blocks = [f"{part.text.strip()} {signature(part.passage, graph, timezone)}" for part in ordered]
    levels = [part.passage.level for part in ordered]
    # D-03: кажемо прямо, лише якщо справді є дві поради одного рівня
    if differs and len(levels) != len(set(levels)):
        blocks.insert(0, DIFFERS)
    return "\n\n".join(blocks)


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Довга відповідь → кілька повідомлень, по абзацах; абзац довший за ліміт — по рядках."""
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        pieces = [paragraph] if len(paragraph) <= limit else _hard_split(paragraph, limit)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]
