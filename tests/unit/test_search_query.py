"""Пошук за словами: tsquery з префіксами замість стемінгу, без стоп-слів."""

import pytest

from forest_bot.qa.search import keyword_query


@pytest.mark.parametrize(
    ("question", "query"),
    [
        # Префікс закриває відмінки: «жолудів» → «жолуді», «внесок» → «внески»
        ("Скільки жолудів внесок?", "жолуд:* | внес:*"),
        ("Хто голова ОСМД?", "голо:* | осм:*"),
        # Числа — як є; слово з апострофом — найдовшою частиною, як його рве Postgres
        ("Що в п'ятницю о 10?", "ятни:* | 10"),
        # Слова, які є майже в кожному документі, нічого не шукають
        ("Що робити в рукавичці в лісі?", None),
    ],
)
def test_keyword_query(question: str, query: str | None) -> None:
    assert keyword_query(question) == query
