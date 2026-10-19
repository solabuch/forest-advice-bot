"""Документ від звіра: розбір → місце → заміна → індексація → повідомлення (S-05)."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import texts
from forest_bot.ingest.upload import INDEX, PREPARE, UploadPipeline
from forest_bot.models import Document, Job, JoinedVia, Upload, UploadStatus
from forest_bot.places import load_places
from forest_bot.repo import uploads, users
from forest_bot.repo.places import sync_places
from forest_bot.repo.uploads import IncomingFile
from tests.integration.test_index import FakeEmbedder

pytestmark = pytest.mark.integration

GRAPH = load_places()
ARCHIVE = Path(__file__).resolve().parents[2] / "data" / "archive"
FEES = (ARCHIVE / "Членські внески рукавички Під дубом.txt").read_bytes()
KABAN, SOVA = 3001, 3002
Sessions = async_sessionmaker[AsyncSession]


@dataclass
class FakeMessenger:
    files: dict[str, bytes] = field(default_factory=dict)
    sent: list[tuple[int, str, InlineKeyboardMarkup | None]] = field(default_factory=list)

    async def send(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> None:
        self.sent.append((chat_id, text, markup))

    async def download(self, file_id: str) -> bytes:
        return self.files[file_id]

    def texts_to(self, chat_id: int) -> list[str]:
        return [text for to, text, _ in self.sent if to == chat_id]


@pytest.fixture
async def pipeline(sessions: Sessions) -> UploadPipeline:
    return await make_pipeline(sessions)


async def make_pipeline(sessions: Sessions) -> UploadPipeline:
    """Місця в БД, Кабан і Сова вже назвалися; Telegram і embeddings — фейкові."""
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
        for user_id, name in ((KABAN, "Кабан"), (SOVA, "Сова")):
            await users.set_name(
                session, await users.admit(session, user_id, JoinedVia.INVITE), name
            )
    return UploadPipeline(sessions, FakeMessenger(), FakeEmbedder(), GRAPH, llm=None)


def messenger(pipeline: UploadPipeline) -> FakeMessenger:
    assert isinstance(pipeline.messenger, FakeMessenger)
    return pipeline.messenger


async def upload(
    pipeline: UploadPipeline, data: bytes, name: str, user: int = KABAN, batch: str | None = None
) -> int:
    file_id = f"tg-{name}-{len(messenger(pipeline).files)}"
    messenger(pipeline).files[file_id] = data
    async with pipeline.sessions.begin() as session:
        created = await uploads.create(
            session,
            user_id=user,
            chat_id=user,
            batch=batch,
            file=IncomingFile(file_id, name, len(data), None),
            status=UploadStatus.QUEUED,
        )
        return created.id


async def state(pipeline: UploadPipeline, upload_id: int) -> Upload:
    async with pipeline.sessions() as session:
        return await session.get_one(Upload, upload_id)


async def run_index_jobs(pipeline: UploadPipeline) -> None:
    """Що зробив би worker: усі задачі індексації з черги."""
    async with pipeline.sessions() as session:
        payloads = list(await session.scalars(select(Job.payload).where(Job.kind == INDEX)))
    for payload in payloads:
        await pipeline.index(payload["upload_id"])


async def test_document_is_indexed_and_author_told(pipeline: UploadPipeline) -> None:
    # R-20: автор дізнається, що документ уже шукається
    upload_id = await upload(pipeline, FEES, "внески.txt")
    await pipeline.prepare(upload_id)
    draft = await state(pipeline, upload_id)
    # Місце — з фрази адресата, назва — із заголовка документа (ADR-16, D-17)
    assert (draft.status, draft.place_id) == (UploadStatus.INDEXING, "pid-dubom")
    assert draft.title == "Членські внески рукавички «Під дубом»"

    await run_index_jobs(pipeline)
    done = await state(pipeline, upload_id)
    assert done.status is UploadStatus.DONE
    async with pipeline.sessions() as session:
        document = await session.get_one(Document, done.document_id)
    assert (document.is_current, document.sender_id, document.place_id) == (
        True,
        KABAN,
        "pid-dubom",
    )
    chat_id, text, markup = messenger(pipeline).sent[-1]
    assert chat_id == KABAN
    assert text == texts.READY.format(title=draft.title, place="рукавички «Під дубом»")
    # Назва й місце — з кнопками «Змінити» (D-17)
    assert markup is not None
    assert [b.text for b in markup.inline_keyboard[0]] == [texts.CHANGE_TITLE, texts.CHANGE_PLACE]


async def test_same_file_twice_is_rejected(pipeline: UploadPipeline) -> None:
    first = await upload(pipeline, FEES, "внески.txt")
    await pipeline.prepare(first)
    await run_index_jobs(pipeline)
    second = await upload(pipeline, FEES, "копія.txt", user=SOVA)
    await pipeline.prepare(second)
    assert (await state(pipeline, second)).status is UploadStatus.CANCELLED
    assert messenger(pipeline).texts_to(SOVA) == [
        texts.DUPLICATE.format(
            title="Членські внески рукавички «Під дубом»", place="рукавички «Під дубом»"
        )
    ]


async def test_unreadable_file_is_explained(pipeline: UploadPipeline) -> None:
    upload_id = await upload(pipeline, b"not a zip", "внески.docx")
    await pipeline.prepare(upload_id)
    draft = await state(pipeline, upload_id)
    assert draft.status is UploadStatus.FAILED
    assert messenger(pipeline).texts_to(KABAN)[-1].startswith("Не вдалося прочитати «внески.docx»")


async def test_unclear_place_asks_author(pipeline: UploadPipeline) -> None:
    # R-08: місце не вгадуємо — питаємо автора
    text = "Горіхи\n\nГоріхи збирайте, коли самі падають. Не трусіть гілок.".encode()
    upload_id = await upload(pipeline, text, "горіхи.txt")
    await pipeline.prepare(upload_id)
    draft = await state(pipeline, upload_id)
    assert draft.status is UploadStatus.NEEDS_PLACE
    _, question, markup = messenger(pipeline).sent[-1]
    assert question == texts.ASK_PLACE.format(title="Горіхи")
    assert markup is not None
    buttons = [b.text for row in markup.inline_keyboard for b in row]
    assert "Ліс" in buttons
    assert "«У ліщині»" in buttons

    # Звір обрав «У ліщині» — далі той самий шлях без повторного розбору
    async with pipeline.sessions.begin() as session:
        draft = await session.get_one(Upload, upload_id)
        draft.place_id = "u-lishchyni"
        await pipeline.after_place(session, draft)
    await run_index_jobs(pipeline)
    assert (await state(pipeline, upload_id)).status is UploadStatus.DONE


async def test_unknown_mitten_is_named(pipeline: UploadPipeline) -> None:
    text = "Листопад\n\nШановні мешканці рукавички «Під кленом»! Листя згрібаємо в суботу.".encode()
    upload_id = await upload(pipeline, text, "листопад.txt")
    await pipeline.prepare(upload_id)
    assert (await state(pipeline, upload_id)).status is UploadStatus.NEEDS_PLACE
    _, question, markup = messenger(pipeline).sent[-1]
    assert question == texts.ASK_UNKNOWN_PLACE.format(title="Листопад", name="Під кленом")
    assert markup is not None


async def test_several_places_ask_to_split(pipeline: UploadPipeline) -> None:
    # R-07, R-16: одне місце на документ — інакше розділити
    text = "Внески\n\nДля мешканців рукавичок «Під дубом» і «У ліщині». Внесок — 4 жолуді.".encode()
    upload_id = await upload(pipeline, text, "внески.txt")
    await pipeline.prepare(upload_id)
    assert (await state(pipeline, upload_id)).status is UploadStatus.CANCELLED
    assert "розділи його" in messenger(pipeline).texts_to(KABAN)[-1]


async def test_new_version_asks_and_notifies_previous_author(pipeline: UploadPipeline) -> None:
    first = await upload(pipeline, FEES, "внески.txt", user=SOVA)
    await pipeline.prepare(first)
    await run_index_jobs(pipeline)
    old_document_id = (await state(pipeline, first)).document_id

    newer = FEES.replace("4 жолуді".encode(), "5 жолудів".encode())
    second = await upload(pipeline, newer, "внески-2026.txt")
    await pipeline.prepare(second)
    draft = await state(pipeline, second)
    # Схожа назва, те саме місце → питаємо, а не замінюємо (D-37)
    assert draft.status is UploadStatus.NEEDS_CHOICE
    assert draft.candidate_ids == [old_document_id]
    assert (
        "новий варіант документа «Членські внески рукавички «Під дубом»»"
        in (messenger(pipeline).texts_to(KABAN)[-1])
    )

    async with pipeline.sessions.begin() as session:
        draft = await session.get_one(Upload, second)
        draft.replaces_id = old_document_id
        await pipeline.start_index(session, draft)
    await run_index_jobs(pipeline)

    async with pipeline.sessions() as session:
        old = await session.get_one(Document, old_document_id)
        new = await session.get_one(Document, (await state(pipeline, second)).document_id)
    assert (old.is_current, new.is_current, new.lineage_id) == (False, True, old.lineage_id)
    assert messenger(pipeline).texts_to(KABAN)[-1].startswith("Готово ✅")
    # Сові — що замінено, ким і коли (D-42)
    assert (
        messenger(pipeline)
        .texts_to(SOVA)[-1]
        .startswith(
            "Твій документ «Членські внески рукавички «Під дубом»» (рукавички «Під дубом») "
            "замінено новим варіантом від Кабан"
        )
    )


async def test_batch_ends_with_summary(pipeline: UploadPipeline) -> None:
    # D-53: кілька документів — по черзі, наприкінці підсумок
    nuts = "Горіхи\n\nДля мешканців рукавички «У ліщині». Горіхи — у вересні.".encode()
    ids = [
        await upload(pipeline, FEES, "внески.txt", batch="album-1"),
        await upload(pipeline, nuts, "горіхи.txt", batch="album-1"),
        await upload(pipeline, b"broken", "бите.docx", batch="album-1"),
    ]
    for upload_id in ids:
        await pipeline.prepare(upload_id)
    await run_index_jobs(pipeline)
    assert messenger(pipeline).texts_to(KABAN)[-1] == (
        "Усе прочитав. Уже шукаються: «Членські внески рукавички «Під дубом»», «Горіхи». "
        "Не вдалося: «бите.docx»."
    )


async def test_prepare_is_idempotent(pipeline: UploadPipeline) -> None:
    upload_id = await upload(pipeline, FEES, "внески.txt")
    await pipeline.prepare(upload_id)
    sent = len(messenger(pipeline).sent)
    # Повтор задачі після рестарту — нічого вдруге
    await pipeline.prepare(upload_id)
    assert len(messenger(pipeline).sent) == sent
    async with pipeline.sessions() as session:
        assert (await session.scalar(select(Job.kind).where(Job.kind == PREPARE))) is None
