"""Місця лісу: DAG з розмови з Мишкою (L25, L27) і доречні області для пошуку."""

from pathlib import Path

import pytest

from forest_bot.places import Level, PlaceGraph, PlaceKind, PlacesError, load_places, normalize_name

GRAPH = load_places()

F, M, P = Level.FOREST, Level.MITTEN, Level.PART

# Очікувані доречні області для кожного з 12 місць: (id, рівень) від конкретнішого
APPLICABLE = {
    # R-27: питання про весь ліс → лише поради лісу
    "forest": [("forest", F)],
    # R-17: питання про частину → частина + ліс, без порад рукавичок
    "coniferous": [("coniferous", P), ("forest", F)],
    "deciduous": [("deciduous", P), ("forest", F)],
    "river": [("river", P), ("forest", F)],
    # Рукавичка → вона, її частини, ліс (L25)
    "pid-yalynoiu": [("pid-yalynoiu", M), ("coniferous", P), ("forest", F)],
    "u-sosnakh": [("u-sosnakh", M), ("coniferous", P), ("forest", F)],
    "bilia-murashnyka": [("bilia-murashnyka", M), ("coniferous", P), ("forest", F)],
    "pid-dubom": [("pid-dubom", M), ("deciduous", P), ("forest", F)],
    "u-lishchyni": [("u-lishchyni", M), ("deciduous", P), ("forest", F)],
    # «Під вербою» — у двох частинах одразу: DAG, а не дерево
    "pid-verboiu": [("pid-verboiu", M), ("deciduous", P), ("river", P), ("forest", F)],
    "bilia-bobrovoi-hrebli": [("bilia-bobrovoi-hrebli", M), ("river", P), ("forest", F)],
    # «На узліссі» — у жодній частині: лише власні й загальні поради (L27)
    "na-uzlissi": [("na-uzlissi", M), ("forest", F)],
}


def test_forest_shape_matches_mishka() -> None:
    assert len(GRAPH.of_kind(PlaceKind.FOREST)) == 1
    assert [p.name for p in GRAPH.of_kind(PlaceKind.PART)] == [
        "Хвойний ліс",
        "Листяний ліс",
        "Біля річки",
    ]
    assert [p.name for p in GRAPH.of_kind(PlaceKind.MITTEN)] == [
        "Під ялиною",
        "У соснах",
        "Біля мурашника",
        "Під дубом",
        "У ліщині",
        "Під вербою",
        "Біля бобрової греблі",
        "На узліссі",
    ]


@pytest.mark.parametrize(("place_id", "expected"), APPLICABLE.items())
def test_applicable(place_id: str, expected: list[tuple[str, Level]]) -> None:
    assert [(a.place.id, a.level) for a in GRAPH.applicable(place_id)] == expected


def test_every_place_covered() -> None:
    assert set(APPLICABLE) == {p.id for p in GRAPH.places}


def test_citation_matches_etalon() -> None:
    # Еталон Мишки: «— з документа «Членські внески» рукавички «Під дубом»» (L21)
    assert GRAPH.get("pid-dubom").citation == "рукавички «Під дубом»"


def test_aliases_lead_to_one_place() -> None:
    aliases = GRAPH.aliases()
    assert aliases["дубова"].id == "pid-dubom"
    assert aliases["прибережна"].id == "river"
    assert aliases[normalize_name("«Під вербою»")].id == "pid-verboiu"


def test_unknown_place() -> None:
    with pytest.raises(KeyError, match="невідоме місце"):
        GRAPH.applicable("pid-sosnoiu")


VALID = """
forest: {id: forest, name: Ліс, citation: всього лісу}
parts:
  - {id: river, name: Біля річки, citation: біля річки}
mittens:
  - {id: pid-verboiu, name: Під вербою, citation: «Під вербою», parts: [river]}
"""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (VALID.replace("parts: [river]", "parts: [lake]"), "невідомих частин: lake"),
        (VALID.replace("id: pid-verboiu", "id: river"), "id повторюються: river"),
        (VALID.replace("parts: [river]}", "parts: [river], aliases: [біля річки]}"), "веде і до"),
        (VALID.replace("id: river", "id: Річка"), "лише латиниця"),
        (VALID.replace("citation: всього лісу", ""), "forest.citation"),
        ("forest: [", "невалідний YAML"),
    ],
)
def test_invalid_files_explained(text: str, message: str) -> None:
    with pytest.raises(PlacesError, match=message):
        PlaceGraph.from_yaml(text)


def test_custom_file(tmp_path: Path) -> None:
    path = tmp_path / "places.yaml"
    path.write_text(VALID, encoding="utf-8")
    assert [a.place.id for a in load_places(path).applicable("pid-verboiu")] == [
        "pid-verboiu",
        "river",
        "forest",
    ]
    with pytest.raises(PlacesError, match="не вдалося прочитати"):
        load_places(tmp_path / "missing.yaml")
