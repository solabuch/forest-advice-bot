"""Чанкінг: межі розділів, довгі абзаци з перекриттям, контекстний заголовок."""

import pytest

from forest_bot.ingest import chunk as chunk_module
from forest_bot.ingest.chunk import Piece, chunk, context_header
from forest_bot.ingest.parse import Block, ParsedDocument


def words(text: str) -> int:
    """Лічильник «токенів» для тестів — слова."""
    return len(text.split())


def doc(*blocks: Block) -> ParsedDocument:
    return ParsedDocument(blocks=blocks, title="Внески")


def test_sections_are_not_mixed_and_headings_are_not_text() -> None:
    document = doc(
        Block("Внески", "Внески", heading=True),
        Block("Шановні мешканці рукавички «Під дубом»!", "Внески"),
        Block("Основне", "Основне", heading=True),
        Block("Внесок — 4 жолуді.", "Основне"),
        Block("Здавати до 10 числа.", "Основне"),
        Block("Заміна", "Заміна", heading=True, page=2),
        Block("Замість 4 жолудів — 2 горіхи.", "Заміна", page=2),
    )
    assert chunk(document, words) == [
        Piece("Шановні мешканці рукавички «Під дубом»!", "Внески", None),
        Piece("Внесок — 4 жолуді.\nЗдавати до 10 числа.", "Основне", None),
        Piece("Замість 4 жолудів — 2 горіхи.", "Заміна", 2),
    ]


def test_paragraphs_are_grouped_up_to_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chunk_module, "TARGET_TOKENS", 6)
    document = doc(Block("один два три", "S"), Block("чотири п'ять", "S"), Block("шість сім", "S"))
    assert [p.text for p in chunk(document, words)] == ["один два три\nчотири п'ять", "шість сім"]


def test_long_paragraph_is_split_with_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chunk_module, "TARGET_TOKENS", 8)
    monkeypatch.setattr(chunk_module, "MAX_TOKENS", 10)
    text = "Перше речення тут. Друге речення тут. Третє речення тут. Четверте речення тут."
    pieces = chunk(doc(Block(text, "S", page=3)), words)
    assert [p.text for p in pieces] == [
        "Перше речення тут. Друге речення тут.",
        "Друге речення тут. Третє речення тут.",
        "Третє речення тут. Четверте речення тут.",
    ]
    assert {(p.section, p.page) for p in pieces} == {("S", 3)}


def test_empty_document_has_no_pieces() -> None:
    assert chunk(doc(Block("Лише заголовок", "Лише заголовок", heading=True)), words) == []


def test_context_header() -> None:
    assert (
        context_header("Членські внески", "рукавички «Під дубом»", "Заміна")
        == "«Членські внески» — рукавички «Під дубом». Заміна."
    )
    # Розділ, що збігається з назвою, не повторюємо
    assert context_header("Внески", "всього лісу", "Внески") == "«Внески» — всього лісу."
    assert context_header("Внески", "всього лісу", None) == "«Внески» — всього лісу."
