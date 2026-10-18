"""Документ → фрагменти для пошуку (D-18).

Фрагмент — абзаци одного розділу, склеєні до ~TARGET токенів моделі embeddings.
Межі розділів не перетинаються: у підписі великого документа видно розділ і
сторінку. Абзац довший за межу ріжеться за реченнями з перекриттям в одне речення,
щоб думка на стику не загубилася. Документи архіву — на одну сторінку, тож
зазвичай це 1–3 фрагменти.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from forest_bot.ingest.parse import Block, ParsedDocument

# Модель бачить 512 токенів разом із префіксом і контекстним заголовком
TARGET_TOKENS = 300
MAX_TOKENS = 420

_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Piece:
    text: str
    section: str | None
    page: int | None


def _split_long(text: str, count: Callable[[str], int]) -> list[str]:
    """Довгий абзац → шматки до TARGET токенів за реченнями, з перекриттям в одне речення."""
    sentences = [s for s in _SENTENCE.split(text) if s.strip()]
    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if current and count(" ".join([*current, sentence])) > TARGET_TOKENS:
            pieces.append(" ".join(current))
            current = [current[-1]] if count(current[-1]) < TARGET_TOKENS // 2 else []
        current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    # Одне речення довше за межу — модель обріже сама; краще так, ніж губити текст
    return pieces


def chunk(document: ParsedDocument, count: Callable[[str], int]) -> list[Piece]:
    """Блоки документа → фрагменти; заголовки — лише в `section`, не в тексті."""
    pieces: list[Piece] = []
    group: list[Block] = []

    def flush() -> None:
        if group:
            text = "\n".join(block.text for block in group)
            pieces.append(Piece(text, group[0].section, group[0].page))
            group.clear()

    for block in document.blocks:
        if block.heading:
            continue
        if count(block.text) > MAX_TOKENS:
            flush()
            pieces.extend(
                Piece(part, block.section, block.page) for part in _split_long(block.text, count)
            )
            continue
        if group and (
            block.section != group[0].section
            or count("\n".join(b.text for b in [*group, block])) > TARGET_TOKENS
        ):
            flush()
        group.append(block)
    flush()
    return pieces


def context_header(title: str, place: str, section: str | None) -> str:
    """Заголовок, з яким фрагмент іде в модель: «Назва» — для кого, розділ.

    Фрагмент «Внесок — 4 жолуді» сам не каже, що це внески «Під дубом»; з заголовком —
    каже, і питання «скільки платити під дубом» його знаходить.
    """
    header = f"«{title}» — {place}"
    if section and section != title:
        header += f". {section}"
    return header + "."
