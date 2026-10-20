"""Минулий строк — прямо, незалежно від моделі (R-28, D-47).

Мишка (20.10): «якщо строк уже минув, бот має прямо сказати про це навіть у відповіді
запасної моделі, не лише назвати дату». Тож строк у тексті відповіді перевіряє код:
«до середини серпня», «до 15 серпня», «до кінця вересня», «до вересня». Строк без місяця
(«до 10 числа» — щомісяця) не чіпаємо.

Року в порадах немає — вони сезонні. Строк, що минув за останні пів року, — минув цього
сезону; давніший («до кінця лютого» в жовтні) — радше про наступний, тож його не чіпаємо.
"""

import calendar
import re
from datetime import date, timedelta

from forest_bot import texts

MONTHS = (
    *("січня", "лютого", "березня", "квітня", "травня", "червня"),
    *("липня", "серпня", "вересня", "жовтня", "листопада", "грудня"),
)
# Минув давніше — це строк наступного сезону
SEASON = timedelta(days=183)

_DEADLINE = re.compile(
    r"\bдо\s+(?:(?P<day>\d{1,2})\s+|(?P<part>початку|середини|кінця)\s+)?"
    rf"(?P<month>{'|'.join(MONTHS)})\b",
    re.IGNORECASE,
)
# Модель уже сказала, що пізно, — вдруге не повторюємо
_WARNED = re.compile(
    r"минув|минула|минуло|уже пізно|вже пізно|запізно|закінчи|не знайдеш|уже не|вже не",
    re.IGNORECASE,
)


def last_day(match: re.Match[str], year: int) -> date | None:
    """Останній день, коли порада ще діє; неможлива дата («до 31 лютого») → None."""
    month = MONTHS.index(match["month"].lower()) + 1
    try:
        if match["day"]:
            return date(year, month, int(match["day"]))
    except ValueError:
        return None
    match match["part"] and match["part"].lower():
        case "середини":
            return date(year, month, 15)
        case "кінця":
            return date(year, month, calendar.monthrange(year, month)[1])
        case _:  # «до вересня», «до початку вересня» — останній день серпня
            return date(year, month, 1) - timedelta(days=1)


def warn_if_passed(text: str, today: date) -> str:
    """Дописує до частини відповіді, що названий у ній строк уже минув."""
    if _WARNED.search(text):
        return text
    for match in _DEADLINE.finditer(text):
        last = last_day(match, today.year)
        if last is not None and timedelta(0) < today - last <= SEASON:
            body = text.rstrip()
            if not body.endswith((".", "!", "?", "…")):
                body += "."
            when = f"{today.day} {MONTHS[today.month - 1]}"
            return f"{body} {texts.DEADLINE_PASSED.format(deadline=match[0], today=when)}"
    return text
