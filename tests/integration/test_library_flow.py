"""«Мої документи» з видаленням (D-05, D-43), «Документи місця» (D-25) і місця лісу —
наскрізно через Dispatcher, як у Telegram."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import InlineKeyboardMarkup

from forest_bot import texts
from forest_bot.keyboards import LibraryAction, UploadAction
from forest_bot.models import Document
from forest_bot.repo.places import sync_places
from tests.integration.test_search import add
from tests.integration.test_upload_flow import FEES, GRAPH, Forest, Sessions, ready_fees

pytestmark = pytest.mark.integration

TITLE = "Членські внески рукавички «Під дубом»"
UNDER_OAK = "рукавички «Під дубом»"


@pytest.fixture
async def forest(sessions: Sessions) -> Forest:
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
    return Forest(sessions)


def buttons(forest: Forest) -> list[str]:
    markup = forest.api.sent()[-1].reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    return [button.text for row in markup.inline_keyboard for button in row]


def alert(forest: Forest) -> str | None:
    answers = [c for c in forest.api.calls if isinstance(c, AnswerCallbackQuery)]
    return answers[-1].text


async def stored(forest: Forest, document_id: int) -> Document:
    async with forest.sessions() as session:
        return await session.get_one(Document, document_id)


async def archived(forest: Forest, place_id: str, title: str) -> int:
    async with forest.sessions.begin() as session:
        return (await add(session, place_id, title)).id


async def test_my_documents_and_delete(forest: Forest) -> None:
    upload = await ready_fees(forest)
    assert upload.document_id is not None
    await forest.say("/my")
    item = texts.MY_ITEM.format(title=TITLE, place=UNDER_OAK, status=texts.STATUS_CURRENT)
    assert forest.replies()[-1] == (
        f"{texts.MY_DOCUMENTS.format(items=item)}\n\n{texts.MY_DELETE_HINT}"
    )
    assert buttons(forest) == [texts.DELETE_BUTTON.format(title=TITLE[:40])]

    await forest.press(LibraryAction(action="delete", id=upload.document_id))
    assert forest.replies()[-1] == texts.ASK_DELETE.format(title=TITLE)
    await forest.press(LibraryAction(action="delete_yes", id=upload.document_id))
    assert forest.replies()[-1] == texts.DELETED.format(title=TITLE)
    document = await stored(forest, upload.document_id)
    assert document.deleted_at is not None
    assert not document.is_current

    await forest.say("/my")
    assert forest.replies()[-1] == texts.MY_EMPTY


async def test_keep_document(forest: Forest) -> None:
    upload = await ready_fees(forest)
    assert upload.document_id is not None
    await forest.press(LibraryAction(action="delete_no", id=upload.document_id))
    assert forest.replies()[-1] == texts.KEPT.format(title=TITLE)
    assert (await stored(forest, upload.document_id)).is_current


async def test_deleting_new_version_brings_back_previous(forest: Forest) -> None:
    # D-43: Зайчик замінив архівні внески своїм варіантом, потім видалив його
    previous = await archived(forest, "pid-dubom", TITLE)
    await forest.send_file("внески.txt", FEES)
    await forest.say("Зайчик")
    await forest.work()
    upload = await forest.upload()
    await forest.press(UploadAction(id=upload.id, action="replace", value=str(previous)))
    await forest.work()
    upload = await forest.upload()
    assert upload.document_id is not None
    assert not (await stored(forest, previous)).is_current

    await forest.say("/my")
    assert texts.STATUS_CURRENT in forest.replies()[-1]
    await forest.press(LibraryAction(action="delete_yes", id=upload.document_id))
    assert forest.replies()[-1] == texts.DELETED_RESTORED.format(
        title=TITLE, previous=TITLE, source=texts.FROM_ARCHIVE
    )
    assert (await stored(forest, previous)).is_current


async def test_someone_elses_document_is_not_deleted(forest: Forest) -> None:
    document_id = await archived(forest, "pid-dubom", TITLE)
    await forest.press(LibraryAction(action="delete_yes", id=document_id))
    assert alert(forest) == texts.NOT_YOUR_DOCUMENT
    assert (await stored(forest, document_id)).is_current


async def test_place_documents_follow_the_place_graph(forest: Forest) -> None:
    for place_id in ("pid-verboiu", "deciduous", "river", "coniferous", "pid-dubom", "forest"):
        await archived(forest, place_id, f"Порада {place_id}")
    async with forest.sessions.begin() as session:
        old = await add(session, "forest", "Пожежа 2025")
        await add(session, "forest", "Пожежа 2026", lineage=old)

    await forest.say("/docs")
    assert forest.replies()[-1] == texts.DOCS_ASK_PLACE
    await forest.press(LibraryAction(action="place", value="pid-verboiu"))
    # Рукавичка у двох частинах — документи обох; чужа частина, сусідня рукавичка
    # й замінений варіант — ні (R-10, R-15)
    archive = texts.FROM_ARCHIVE
    assert forest.replies()[-1] == "\n\n".join(
        [
            texts.PLACE_DOCUMENTS.format(place="рукавички «Під вербою»"),
            f"Для рукавички «Під вербою»:\n• «Порада pid-verboiu» — {archive}",
            f"Для листяного лісу:\n• «Порада deciduous» — {archive}",
            f"Для частини лісу біля річки:\n• «Порада river» — {archive}",
            f"Для всього лісу:\n• «Пожежа 2026» — {archive}\n• «Порада forest» — {archive}",
        ]
    )


async def test_place_documents_show_who_sent(forest: Forest) -> None:
    await ready_fees(forest)
    await forest.press(LibraryAction(action="place", value="pid-dubom"))
    today = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%d.%m.%Y")
    sent = texts.SENT_BY.format(who="Зайчик", date=today)
    assert f"• «{TITLE}» — {sent}" in forest.replies()[-1]


async def test_place_without_documents(forest: Forest) -> None:
    await forest.press(LibraryAction(action="place", value="na-uzlissi"))
    assert forest.replies()[-1] == texts.PLACE_EMPTY.format(place="рукавички «На узліссі»")


async def test_places_with_parts(forest: Forest) -> None:
    await forest.say("/places")
    text = forest.replies()[-1]
    # «Під вербою» — в обох своїх частинах, «На узліссі» — поза частинами (D-21)
    assert text.count("«Під вербою»") == 2
    assert texts.PLACES_OUTSIDE.format(mittens="«На узліссі»") in text
    assert "• Листяний ліс: «Під дубом», «У ліщині», «Під вербою»" in text
