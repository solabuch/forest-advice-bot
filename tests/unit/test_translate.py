"""Документ іншою мовою (D-30): шукається за перекладом, у БД — оригінал."""

from collections.abc import Sequence

import pytest

from forest_bot.ingest.translate import is_foreign


@pytest.mark.parametrize(
    ("texts", "foreign"),
    [
        (["Chanterelles grow under the old spruce, 50 steps west of the anthill."], True),
        (["Лисички ростуть під старою ялиною, за 50 кроків від мурашника."], False),
        # Українська з англійськими назвами — своя
        (["Внески — 4 жолуді. Збирає Кабан, рахунок у Google Sheets і Telegram."], False),
        (["OK"], False),
    ],
)
def test_is_foreign(texts: Sequence[str], foreign: bool) -> None:
    # D-30: документ іншою мовою впізнається за часткою кирилиці
    assert is_foreign(texts) is foreign
