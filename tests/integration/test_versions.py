"""Версії документів: архів → заміна з Telegram → синхронізація архіву → видалення → відкат."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Document, DocumentSource, DocumentStatus, JoinedVia
from forest_bot.places import load_places
from forest_bot.repo import documents, users
from forest_bot.repo.documents import DocumentError, FileInfo
from forest_bot.repo.places import sync_places
from forest_bot.repo.users import NameTakenError

pytestmark = pytest.mark.integration

GRAPH = load_places()
KABAN, ZAYCHYK = 1001, 1002


def file(sha: str, **extra: str) -> FileInfo:
    return FileInfo(file_name=f"{sha}.txt", size_bytes=100, file_sha256=sha * 64, **extra)


@pytest.fixture
async def forest(session: AsyncSession) -> None:
    await sync_places(session, GRAPH)
    for user_id, name in ((KABAN, "Кабан"), (ZAYCHYK, "Зайчик")):
        await users.set_name(session, await users.admit(session, user_id, JoinedVia.INVITE), name)


async def archive_fees(session: AsyncSession) -> Document:
    doc = await documents.create(
        session,
        place_id="pid-dubom",
        title="Членські внески рукавички Під дубом",
        source=DocumentSource.ARCHIVE,
        file=file("a", archive_file_id="drive-1", archive_modified="Oct 13"),
    )
    assert await documents.activate(session, doc) is None
    return doc


@pytest.mark.usefixtures("forest")
async def test_version_lifecycle(session: AsyncSession) -> None:
    archived = await archive_fees(session)

    # Кабан надсилає нові внески: схожа назва, те саме місце → кандидат на заміну (D-37)
    candidates = await documents.replacement_candidates(session, "pid-dubom", "Членські внески")
    assert [c.id for c in candidates] == [archived.id]
    new = await documents.create(
        session,
        place_id="pid-dubom",
        title="Членські внески",
        source=DocumentSource.TELEGRAM,
        sender_id=KABAN,
        file=file("b"),
        lineage_id=archived.lineage_id,
    )
    assert (new.version, new.is_current, new.status) == (2, False, DocumentStatus.PROCESSING)
    # Поки нова версія індексується, діє стара
    assert [d.id for d in await documents.current_for_place(session, GRAPH, "pid-dubom")] == [
        archived.id
    ]

    # Проіндексовано → нова чинна, стара замінена; повертається замінена (для сповіщення D-42)
    assert await documents.activate(session, new) == archived
    assert (archived.is_current, new.is_current) == (False, True)

    # Синхронізація архіву з тим самим файлом не відкочує заміну (R-15)
    assert not await documents.archive_version_changed(session, "drive-1", "a" * 64)
    assert await documents.archive_version_changed(session, "drive-1", "c" * 64)

    # Чуже й архівне видаляти не можна (D-05)
    with pytest.raises(DocumentError, match="лише документ, який надіслав ти"):
        await documents.delete(session, new.id, ZAYCHYK)
    with pytest.raises(DocumentError, match="Лісового архіву"):
        await documents.delete(session, archived.id, KABAN)

    # Кабан видаляє свою версію → знову діє архівна (D-43); історія лишається (D-13)
    assert await documents.delete(session, new.id, KABAN) == archived
    assert archived.is_current
    assert new.deleted_at is not None
    assert await documents.of_user(session, KABAN) == []


@pytest.mark.usefixtures("forest")
async def test_failed_version_keeps_old_one(session: AsyncSession) -> None:
    archived = await archive_fees(session)
    new = await documents.create(
        session,
        place_id="pid-dubom",
        title="Членські внески",
        source=DocumentSource.TELEGRAM,
        sender_id=KABAN,
        file=file("b"),
        lineage_id=archived.lineage_id,
    )
    await documents.mark_failed(session, new, "не вдалося прочитати PDF")
    assert archived.is_current
    assert await documents.of_user(session, KABAN) == [new]


@pytest.mark.usefixtures("forest")
async def test_candidates_only_same_place_and_similar_title(session: AsyncSession) -> None:
    await archive_fees(session)
    assert await documents.replacement_candidates(session, "u-lishchyni", "Членські внески") == []
    assert await documents.replacement_candidates(session, "pid-dubom", "Грибний путівник") == []


@pytest.mark.usefixtures("forest")
async def test_current_for_place_by_priority(session: AsyncSession) -> None:
    for sha, place, title in (
        ("f", "forest", "Календар сезонів"),
        ("d", "deciduous", "Коли падають жолуді"),
        ("m", "pid-dubom", "Членські внески"),
        ("r", "river", "Броди"),
    ):
        doc = await documents.create(
            session, place_id=place, title=title, source=DocumentSource.ARCHIVE, file=file(sha)
        )
        await documents.activate(session, doc)
    found = await documents.current_for_place(session, GRAPH, "pid-dubom")
    # Рукавичка → частина → ліс; поради «Біля річки» не доречні
    assert [d.place_id for d in found] == ["pid-dubom", "deciduous", "forest"]


async def test_names_and_membership(session: AsyncSession) -> None:
    assert not await users.has_members(session)
    first = await users.admit(session, KABAN, JoinedVia.FIRST)
    assert await users.has_members(session)
    assert (await users.admit(session, KABAN, JoinedVia.INVITE)).joined_via is JoinedVia.FIRST

    await users.set_name(session, first, "Кабан")
    other = await users.get_or_create(session, ZAYCHYK)
    with pytest.raises(NameTakenError, match="уже має інший звір"):
        await users.set_name(session, other, "кабан")
    # Зміна власного імені (D-29) — без конфлікту з самим собою
    assert await users.set_name(session, first, "Кабан Старший") == "Кабан Старший"
    assert await users.set_name(session, other, "Кабан") == "Кабан"
