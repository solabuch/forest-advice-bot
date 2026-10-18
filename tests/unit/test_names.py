"""Імена звірів і ключі назв документів — без БД."""

import pytest

from forest_bot.repo.documents import title_key
from forest_bot.repo.users import InvalidNameError, clean_name, name_key


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("  Кабан ", "Кабан"),
        ("Зайчик   Сірий", "Зайчик Сірий"),
        ("Мишка-шкряботушка", "Мишка-шкряботушка"),
        ("Сова", "Сова"),
    ],
)
def test_clean_name(raw: str, name: str) -> None:
    assert clean_name(raw) == name


@pytest.mark.parametrize("raw", ["", "К", "Кабан123", "@kaban", "Кабан!!!", "x" * 61])
def test_invalid_names_explained(raw: str) -> None:
    with pytest.raises(InvalidNameError):
        clean_name(raw)


def test_name_key_ignores_case_and_apostrophes() -> None:
    assert name_key("Зайчик Сірий") == name_key("зайчик сірий")
    assert name_key("Сов’я") == name_key("Сов'я")


def test_title_key() -> None:
    assert title_key("«Членські  внески» — 2026!") == "членські внески 2026"
