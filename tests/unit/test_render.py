"""Текст відповіді: назва в підписі як в еталоні, підписи R-12/D-18, порядок R-24, D-03."""

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from forest_bot.models import DocumentSource
from forest_bot.places import PlaceGraph, load_places
from forest_bot.qa.render import (
    DIFFERS,
    RenderedPart,
    compose,
    short_title,
    signature,
    split_message,
)
from forest_bot.qa.search import Passage

GRAPH: PlaceGraph = load_places()
KYIV = ZoneInfo("Europe/Kyiv")


def passage(
    place_id: str = "pid-dubom", title: str = "Членські внески", **changes: object
) -> Passage:
    base = Passage(
        document_id=1,
        title=title,
        place_id=place_id,
        level=GRAPH.get(place_id).level,
        source=DocumentSource.ARCHIVE,
        sender=None,
        sent_at=datetime(2026, 10, 12, 22, 30, tzinfo=UTC),
        whole=True,
        sections=("Основне",),
        pages=(1,),
        text="…",
        chunk_ids=(1,),
        cosine=0.9,
        keyword=True,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("title", "place_id", "short"),
    [
        # Еталон (§4): місце не повторюється в назві
        ("Членські внески рукавички «Під дубом»", "pid-dubom", "Членські внески"),
        ("Небезпеки поруч із рукавичкою «Під вербою»", "pid-verboiu", "Небезпеки"),
        ("Чергування в рукавичці «У ліщині»", "u-lishchyni", "Чергування"),
        ("Стежки до сусідів від рукавички «У ЛІЩИНІ»", "u-lishchyni", "Стежки до сусідів"),
        ("Статут ОСМД «Під дубом»", "pid-dubom", "Статут ОСМД"),
        ("Грибний путівник хвойного лісу", "coniferous", "Грибний путівник"),
        ("Як утеплити рукавичку в листяному лісі", "deciduous", "Як утеплити рукавичку"),
        ("Небезпеки біля річки", "river", "Небезпеки"),
        # Ліс і назви без місця — як є
        ("Що робити при пожежі", "forest", "Що робити при пожежі"),
        ("Ягоди на узліссі", "na-uzlissi", "Ягоди на узліссі"),
    ],
)
def test_short_title(title: str, place_id: str, short: str) -> None:
    assert short_title(title, GRAPH.get(place_id)) == short


def test_archive_signature_is_title_and_place() -> None:
    signed = signature(passage(title="Членські внески рукавички «Під дубом»"), GRAPH, KYIV)
    assert signed == "— з документа «Членські внески» рукавички «Під дубом»"


def test_sent_document_has_name_and_local_date() -> None:
    sent = passage(source=DocumentSource.TELEGRAM, sender="Сова")
    # 22:30 UTC 12.10 — це вже 13.10 у Києві
    assert signature(sent, GRAPH, KYIV).endswith("(надіслано: Сова, 13.10.2026)")


def test_large_document_has_section_and_pages() -> None:
    large = passage(whole=False, sections=("Гриби", "Ягоди"), pages=(3, 4))
    assert signature(large, GRAPH, KYIV) == (
        "— з документа «Членські внески» рукавички «Під дубом», розділ «Гриби», с. 3–4"
    )


def test_specific_advice_goes_first() -> None:
    general = RenderedPart("Загальна.", passage("forest", "Правила лісу"))
    specific = RenderedPart("Конкретна.", passage("pid-dubom", "Правила"))
    text = compose([general, specific], GRAPH, KYIV, differs=False)
    assert text.index("Конкретна.") < text.index("Загальна.")


def test_differs_only_for_two_advices_of_one_level() -> None:
    river = RenderedPart("Опеньки.", passage("river", "Грибний путівник біля річки"))
    leafy = RenderedPart("Білі.", passage("deciduous", "Грибний путівник листяного лісу"))
    assert compose([river, leafy], GRAPH, KYIV, differs=True).startswith(DIFFERS)
    forest = RenderedPart("Загальна.", passage("forest", "Правила"))
    assert DIFFERS not in compose([river, forest], GRAPH, KYIV, differs=True)


def test_long_answer_is_split_by_paragraphs() -> None:
    text = "\n\n".join(["а" * 3000, "б" * 3000, "в" * 100])
    parts = split_message(text)
    assert [len(p) for p in parts] == [3000, 3102]
    assert all(len(p) <= 4096 for p in split_message("г" * 9000))
