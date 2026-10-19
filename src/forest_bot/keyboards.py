"""Inline-кнопки. Callback несе id чернетки, тож відповідь на кнопку не залежить
від стану в пам'яті й працює після рестарту."""

from collections.abc import Callable, Sequence

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


class AskAction(CallbackData, prefix="qa"):
    """Кнопки під питанням і відповіддю. Саме питання — у `reply_to_message`, а не тут:
    callback обмежений 64 байтами, і так кнопки працюють після рестарту."""

    # place · other · doc · rate
    action: str
    value: str = ""
    id: int = 0


def _places(
    builder: InlineKeyboardBuilder, graph: PlaceGraph, data: Callable[[str], CallbackData]
) -> list[int]:
    """Ліс, частини, рукавички (D-06, R-08) — звір обирає сам; повертає розкладку рядків."""
    builder.button(text=graph.forest.name, callback_data=data(graph.forest.id))
    parts = graph.of_kind(PlaceKind.PART)
    for place in parts:
        builder.button(text=place.name, callback_data=data(place.id))
    mittens = graph.of_kind(PlaceKind.MITTEN)
    for place in mittens:
        builder.button(text=f"«{place.name}»", callback_data=data(place.id))
    return [1, len(parts), *([2] * (len(mittens) // 2 + len(mittens) % 2))]


def place_picker(upload_id: int, graph: PlaceGraph, action: str = "place") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows = _places(
        builder, graph, lambda place_id: UploadAction(id=upload_id, action=action, value=place_id)
    )
    builder.button(text=texts.CANCEL, callback_data=UploadAction(id=upload_id, action="cancel"))
    builder.adjust(*rows, 1)
    return builder.as_markup()


def question_places(graph: PlaceGraph) -> InlineKeyboardMarkup:
    """Місце для питання без місця (D-06)."""
    builder = InlineKeyboardBuilder()
    rows = _places(builder, graph, lambda place_id: AskAction(action="place", value=place_id))
    builder.adjust(*rows)
    return builder.as_markup()


def answer_actions(
    sources: Sequence[tuple[int, str]], qa_id: int | None, *, other_place: bool
) -> InlineKeyboardMarkup:
    """Під відповіддю: документи-джерела (D-28), оцінка (D-50), «Інше місце» (D-24)."""
    builder = InlineKeyboardBuilder()
    rows = []
    for document_id, title in sources:
        builder.button(
            text=texts.DOCUMENT_BUTTON.format(title=title[:40]),
            callback_data=AskAction(action="doc", id=document_id),
        )
        rows.append(1)
    if qa_id is not None:
        builder.button(
            text=texts.USEFUL, callback_data=AskAction(action="rate", value="useful", id=qa_id)
        )
        builder.button(
            text=texts.WRONG, callback_data=AskAction(action="rate", value="wrong", id=qa_id)
        )
        rows.append(2)
    if other_place:
        builder.button(text=texts.OTHER_PLACE, callback_data=AskAction(action="other"))
        rows.append(1)
    builder.adjust(*rows)
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
