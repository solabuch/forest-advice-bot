"""Місця лісу: DAG «ліс → частини → рукавички» і доречні області для пошуку.

Доречність визначає код, а не LLM (R-10, R-17, R-27):
- рукавичка → вона, її частини, ліс («На узліссі» — вона й ліс);
- частина → частина й ліс, без порад рукавичок;
- ліс → лише ліс.
Рівень — пріоритет: конкретніша порада важливіша (R-11).
"""

import re
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class PlaceKind(StrEnum):
    FOREST = "forest"
    PART = "part"
    MITTEN = "mitten"


class Level(IntEnum):
    """Менше значення — конкретніша порада й вищий пріоритет."""

    MITTEN = 0
    PART = 1
    FOREST = 2


LEVEL_BY_KIND = {
    PlaceKind.MITTEN: Level.MITTEN,
    PlaceKind.PART: Level.PART,
    PlaceKind.FOREST: Level.FOREST,
}


class PlacesError(Exception):
    """Файл місць невалідний; повідомлення — для людини."""


@dataclass(frozen=True)
class Place:
    id: str
    kind: PlaceKind
    name: str
    citation: str
    aliases: tuple[str, ...] = ()
    parts: tuple[str, ...] = ()

    @property
    def level(self) -> Level:
        return LEVEL_BY_KIND[self.kind]


@dataclass(frozen=True)
class Applicable:
    place: Place
    level: Level


def normalize_name(text: str) -> str:
    """Ключ для порівняння назв: регістр, апострофи, лапки, пробіли."""
    text = text.casefold().replace("’", "'").replace("ʼ", "'")
    text = re.sub(r"[«»\"“”]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# --- Схема YAML ---


class _PlaceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    citation: str
    aliases: list[str] = []

    @model_validator(mode="after")
    def _slug(self) -> Self:
        if not SLUG_RE.match(self.id):
            raise ValueError(f"id «{self.id}» — лише латиниця в нижньому регістрі, цифри й дефіс")
        return self


class _MittenIn(_PlaceIn):
    parts: list[str]


class _PlacesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forest: _PlaceIn
    parts: list[_PlaceIn]
    mittens: list[_MittenIn]


@dataclass(frozen=True)
class PlaceGraph:
    """Усі місця лісу; будується з places.yaml і валідується при створенні."""

    places: tuple[Place, ...]
    _by_id: dict[str, Place] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {p.id: p for p in self.places})

    @property
    def forest(self) -> Place:
        return next(p for p in self.places if p.kind is PlaceKind.FOREST)

    def __contains__(self, place_id: object) -> bool:
        return place_id in self._by_id

    def get(self, place_id: str) -> Place:
        try:
            return self._by_id[place_id]
        except KeyError:
            raise KeyError(f"невідоме місце: {place_id}") from None

    def of_kind(self, kind: PlaceKind) -> list[Place]:
        return [p for p in self.places if p.kind is kind]

    def applicable(self, place_id: str) -> list[Applicable]:
        """Місця, чиї поради доречні для питання про place_id, від конкретнішого."""
        place = self.get(place_id)
        found = [place, *(self.get(part) for part in place.parts)]
        if place.kind is not PlaceKind.FOREST:
            found.append(self.forest)
        return [Applicable(p, p.level) for p in found]

    def aliases(self) -> dict[str, Place]:
        """Нормалізована назва чи синонім → місце (дані для розпізнавання, D-55)."""
        return {normalize_name(n): p for p in self.places for n in (p.name, *p.aliases)}

    @classmethod
    def from_yaml(cls, text: str) -> Self:
        try:
            raw = _PlacesFile.model_validate(yaml.safe_load(text))
        except yaml.YAMLError as error:
            raise PlacesError(f"файл місць — невалідний YAML: {error}") from None
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise PlacesError(f"файл місць: {problems}") from None

        def build(item: _PlaceIn, kind: PlaceKind, parts: list[str] | None = None) -> Place:
            return Place(
                item.id, kind, item.name, item.citation, tuple(item.aliases), tuple(parts or ())
            )

        places = [
            build(raw.forest, PlaceKind.FOREST),
            *(build(p, PlaceKind.PART) for p in raw.parts),
            *(build(m, PlaceKind.MITTEN, m.parts) for m in raw.mittens),
        ]
        cls._validate(places)
        return cls(tuple(places))

    @staticmethod
    def _validate(places: list[Place]) -> None:
        ids = [p.id for p in places]
        if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
            raise PlacesError(f"файл місць: id повторюються: {', '.join(duplicates)}")
        part_ids = {p.id for p in places if p.kind is PlaceKind.PART}
        for mitten in (p for p in places if p.kind is PlaceKind.MITTEN):
            if unknown := [part for part in mitten.parts if part not in part_ids]:
                names = ", ".join(unknown)
                message = f"«{mitten.name}» належить до невідомих частин: {names}"
                raise PlacesError(f"файл місць: {message}")
        owner: dict[str, Place] = {}
        for place in places:
            for name in (place.name, *place.aliases):
                key = normalize_name(name)
                other = owner.setdefault(key, place)
                if other is not place:
                    raise PlacesError(
                        f"файл місць: назва «{name}» веде і до «{other.name}», і до «{place.name}»"
                    )


DEFAULT_PLACES = files("forest_bot") / "data" / "places.yaml"


def load_places(path: Path | None = None) -> PlaceGraph:
    """Читає places.yaml: свій файл (PLACES_FILE) або вбудований у пакет."""
    if path is None:
        return PlaceGraph.from_yaml(DEFAULT_PLACES.read_text(encoding="utf-8"))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PlacesError(f"не вдалося прочитати файл місць {path}: {error.strerror}") from None
    return PlaceGraph.from_yaml(text)
