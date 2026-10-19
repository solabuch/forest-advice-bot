"""Inline-кнопки. Callback несе id чернетки, тож відповідь на кнопку не залежить
від стану в пам'яті й працює після рестарту."""

from collections.abc import Callable, Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from forest_bot import texts
from forest_bot.places import Place, PlaceGraph, PlaceKind


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


class LibraryAction(CallbackData, prefix="lib"):
    """«Мої документи» й «Документи місця»: id документа чи місце — у самій кнопці."""

    # delete · delete_yes · delete_no · place
    action: str
    id: int = 0
    value: str = ""


class AccountAction(CallbackData, prefix="me"):
    """Підтвердження імені й «Забути мене». Нове ім'я — у `reply_to_message`, а не тут:
    кирилиця не влізе в 64 байти callback."""

    # rename_yes · rename_no · forget_yes · forget_no
    action: str


def confirm(yes: str, no: str, prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=yes, callback_data=AccountAction(action=f"{prefix}_yes"))
    builder.button(text=no, callback_data=AccountAction(action=f"{prefix}_no"))
    builder.adjust(2)
    return builder.as_markup()


class SubscribeAction(CallbackData, prefix="sub"):
    """Підписка на місце (D-26): місце — у самій кнопці."""

    # toggle · off
    action: str
    value: str


def _label(place: Place) -> str:
    return f"«{place.name}»" if place.kind is PlaceKind.MITTEN else place.name


def _places(
    builder: InlineKeyboardBuilder,
    graph: PlaceGraph,
    data: Callable[[str], CallbackData],
    label: Callable[[Place], str] = _label,
) -> list[int]:
    """Ліс, частини, рукавички (D-06, R-08) — звір обирає сам; повертає розкладку рядків."""
    builder.button(text=label(graph.forest), callback_data=data(graph.forest.id))
    parts = graph.of_kind(PlaceKind.PART)
    for place in parts:
        builder.button(text=label(place), callback_data=data(place.id))
    mittens = graph.of_kind(PlaceKind.MITTEN)
    for place in mittens:
        builder.button(text=label(place), callback_data=data(place.id))
    return [1, len(parts), *([2] * (len(mittens) // 2 + len(mittens) % 2))]


def subscriptions(graph: PlaceGraph, subscribed: set[str]) -> InlineKeyboardMarkup:
    """Усі місця; ✅ — підписка є, повторне натискання відписує."""
    builder = InlineKeyboardBuilder()

    def label(place: Place) -> str:
        text = _label(place)
        return texts.SUBSCRIBED_MARK.format(label=text) if place.id in subscribed else text

    rows = _places(
        builder, graph, lambda place_id: SubscribeAction(action="toggle", value=place_id), label
    )
    builder.adjust(*rows)
    return builder.as_markup()


def notice_actions(
    document_id: int, title: str, places: Sequence[tuple[str, str]]
) -> InlineKeyboardMarkup:
    """Під сповіщенням: сам документ (D-28) і відписка однією кнопкою (D-26)."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.DOCUMENT_BUTTON.format(title=title[:40]),
        callback_data=AskAction(action="doc", id=document_id),
    )
    for place_id, citation in places:
        builder.button(
            text=texts.UNSUBSCRIBE_BUTTON.format(place=citation),
            callback_data=SubscribeAction(action="off", value=place_id),
        )
    builder.adjust(1)
    return builder.as_markup()


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


def library_places(graph: PlaceGraph) -> InlineKeyboardMarkup:
    """Місце для «Документи місця» (D-25)."""
    builder = InlineKeyboardBuilder()
    rows = _places(builder, graph, lambda place_id: LibraryAction(action="place", value=place_id))
    builder.adjust(*rows)
    return builder.as_markup()


def my_documents(documents: Sequence[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Кнопка видалення під кожним своїм документом (D-05)."""
    builder = InlineKeyboardBuilder()
    for document_id, title in documents:
        builder.button(
            text=texts.DELETE_BUTTON.format(title=title[:40]),
            callback_data=LibraryAction(action="delete", id=document_id),
        )
    builder.adjust(1)
    return builder.as_markup()


def confirm_delete(document_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.DELETE_YES, callback_data=LibraryAction(action="delete_yes", id=document_id)
    )
    builder.button(
        text=texts.DELETE_NO, callback_data=LibraryAction(action="delete_no", id=document_id)
    )
    builder.adjust(2)
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
