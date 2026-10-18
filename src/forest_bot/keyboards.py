"""Inline-кнопки. Callback несе id чернетки, тож відповідь на кнопку не залежить
від стану в пам'яті й працює після рестарту."""

from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from forest_bot import texts
from forest_bot.places import PlaceGraph, PlaceKind


class UploadAction(CallbackData, prefix="up"):
    id: int
    # place · replace · separate · cancel · refuse_name · title · move
    action: str
    value: str = ""


def place_picker(upload_id: int, graph: PlaceGraph, action: str = "place") -> InlineKeyboardMarkup:
    """Ліс, частини, рукавички (D-06, R-08) — звір обирає сам."""
    builder = InlineKeyboardBuilder()
    forest = graph.forest
    builder.button(
        text=forest.name, callback_data=UploadAction(id=upload_id, action=action, value=forest.id)
    )
    for place in graph.of_kind(PlaceKind.PART):
        builder.button(
            text=place.name, callback_data=UploadAction(id=upload_id, action=action, value=place.id)
        )
    for place in graph.of_kind(PlaceKind.MITTEN):
        builder.button(
            text=f"«{place.name}»",
            callback_data=UploadAction(id=upload_id, action=action, value=place.id),
        )
    builder.button(text=texts.CANCEL, callback_data=UploadAction(id=upload_id, action="cancel"))
    parts = len(graph.of_kind(PlaceKind.PART))
    mittens = len(graph.of_kind(PlaceKind.MITTEN))
    builder.adjust(1, parts, *([2] * (mittens // 2 + mittens % 2)), 1)
    return builder.as_markup()


def replace_choice(upload_id: int, candidates: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Новий варіант чи окремий документ (D-37); кілька кандидатів — вибір."""
    builder = InlineKeyboardBuilder()
    if len(candidates) == 1:
        doc_id, _ = candidates[0]
        builder.button(
            text=texts.REPLACE_ONE,
            callback_data=UploadAction(id=upload_id, action="replace", value=str(doc_id)),
        )
    else:
        for doc_id, title in candidates:
            builder.button(
                text=texts.REPLACE_MANY.format(title=title[:40]),
                callback_data=UploadAction(id=upload_id, action="replace", value=str(doc_id)),
            )
    builder.button(text=texts.SEPARATE, callback_data=UploadAction(id=upload_id, action="separate"))
    builder.adjust(1)
    return builder.as_markup()


def refuse_name() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.REFUSE_NAME_BUTTON, callback_data=UploadAction(id=0, action="refuse_name")
    )
    return builder.as_markup()


def after_ready(upload_id: int) -> InlineKeyboardMarkup:
    """Назва й місце з кнопками «Змінити» (D-17)."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.CHANGE_TITLE, callback_data=UploadAction(id=upload_id, action="title")
    )
    builder.button(text=texts.CHANGE_PLACE, callback_data=UploadAction(id=upload_id, action="move"))
    builder.adjust(2)
    return builder.as_markup()
