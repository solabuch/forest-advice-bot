"""Минулий строк — прямо, незалежно від моделі (R-28, D-47)."""

from datetime import date

import pytest

from forest_bot import texts
from forest_bot.qa.deadlines import warn_if_passed

WEDNESDAY = date(2026, 10, 21)


def test_passed_deadline_is_said_plainly() -> None:
    # R-28: так відповіла запасна модель — строк назвала, а що минув, не сказала
    answer = "Малина росте на вирубці за ярком. Її можна знайти до середини серпня."
    warned = warn_if_passed(answer, WEDNESDAY)
    assert warned == (
        f"{answer} "
        + texts.DEADLINE_PASSED.format(deadline="до середини серпня", today="21 жовтня")
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Внесок здати до 10 числа.",  # щомісячний строк без місяця
        "Жолуді падають до кінця жовтня.",  # ще діє
        "Утеплюйте до першого снігу.",  # не дата
        "Запаси бережіть до кінця лютого.",  # давно — це про наступну зиму
        "Малину можна знайти до середини серпня, тож зараз уже пізно.",  # модель уже сказала
    ],
)
def test_no_warning_when_not_needed(answer: str) -> None:
    assert warn_if_passed(answer, WEDNESDAY) == answer


@pytest.mark.parametrize(
    ("answer", "deadline"),
    [
        ("Збирайте до 15 жовтня", "до 15 жовтня"),
        ("Гриби — до середини жовтня.", "до середини жовтня"),
        ("Ягоди — до вересня.", "до вересня"),
        ("Горіхи — до кінця вересня!", "до кінця вересня"),
    ],
)
def test_each_form_of_deadline(answer: str, deadline: str) -> None:
    warned = warn_if_passed(answer, WEDNESDAY)
    assert warned.endswith(texts.DEADLINE_PASSED.format(deadline=deadline, today="21 жовтня"))


def test_impossible_date_is_ignored() -> None:
    assert warn_if_passed("До 31 лютого.", WEDNESDAY) == "До 31 лютого."
