"""Документ від звіра: розбір → місце → заміна? → індексація → «Готово» (S-05).

Два фонові кроки на чернетці (`uploads`):
- `upload_prepare` — завантажити з Telegram, sha256 (A-09), розібрати, визначити місце
  й назву, знайти кандидатів на заміну;
- `upload_index` — створити документ, проіндексувати й зробити чинним.

Між ними — рішення автора кнопками: місце, якщо з тексту не зрозуміле (R-08), і
«замінити чи окремий» (D-37). Без підтвердження нічого не замінюється.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import jobs, keyboards, texts
from forest_bot.ingest.index import PassageEmbedder, index_document
from forest_bot.ingest.parse import ParsedDocument, ParseError, parse
from forest_bot.ingest.place import detect_place
from forest_bot.llm.client import GroqClient
from forest_bot.models import Document, DocumentSource, Upload, UploadStatus, User
from forest_bot.places import PlaceGraph
from forest_bot.repo import documents
from forest_bot.repo import uploads as upload_repo
from forest_bot.repo.documents import FileInfo

logger = logging.getLogger(__name__)

PREPARE, INDEX = "upload_prepare", "upload_index"


class Messenger(Protocol):
    async def send(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> None: ...

    async def download(self, file_id: str) -> bytes: ...


@dataclass
class UploadPipeline:
    sessions: async_sessionmaker[AsyncSession]
    messenger: Messenger
    embedder: PassageEmbedder
    graph: PlaceGraph
    llm: GroqClient | None
    wake: Callable[[], None] = lambda: None

    def place_name(self, place_id: str) -> str:
        return self.graph.get(place_id).citation

    async def describe(self, session: AsyncSession, document: Document) -> str:
        """«Назва» (рукавички «Під дубом», від Кабан, 12.10) — щоб звір упізнав документ."""
        place = self.place_name(document.place_id)
        if document.source is DocumentSource.ARCHIVE:
            return f"«{document.title}» ({place}, з «Лісового архіву»)"
        sender = await session.get(User, document.sender_id) if document.sender_id else None
        who = sender.real_name if sender and sender.real_name else "звіра"
        return f"«{document.title}» ({place}, від {who}, {document.sent_at:%d.%m})"

    # --- Крок 1: розбір ---

    async def prepare(self, upload_id: int) -> None:
        async with self.sessions() as session:
            upload = await session.get(Upload, upload_id)
            if upload is None or upload.status is not UploadStatus.QUEUED:
                return  # уже оброблено (повтор задачі після рестарту)
            file_id, name, chat_id = upload.telegram_file_id, upload.file_name, upload.chat_id

        data = await self.messenger.download(file_id)
        sha256 = hashlib.sha256(data).hexdigest()
        async with self.sessions.begin() as session:
            upload = await session.get_one(Upload, upload_id, with_for_update=True)
            upload.file_sha256 = sha256
            if (same := await documents.find_by_sha(session, sha256)) is not None:
                upload.status = UploadStatus.CANCELLED
                place = self.place_name(same.place_id)
                await self.messenger.send(
                    chat_id, texts.DUPLICATE.format(title=same.title, place=place)
                )
                await self._summary(session, upload)
                return

        try:
            parsed = await asyncio.to_thread(parse, data, name)
        except ParseError as error:
            await self._fail(upload_id, str(error))
            return
        decision = await detect_place(parsed, self.graph, self.llm)

        async with self.sessions.begin() as session:
            upload = await session.get_one(Upload, upload_id, with_for_update=True)
            upload.parsed = parsed.to_json()
            upload.title = (parsed.title or name)[:300]
            if decision.several:
                upload.status = UploadStatus.CANCELLED
                places = ", ".join(self.graph.get(p).name for p in decision.several)
                await self.messenger.send(
                    chat_id, texts.SEVERAL_PLACES.format(name=name, places=places)
                )
                await self._summary(session, upload)
            elif decision.place_id is None:
                upload.status = UploadStatus.NEEDS_PLACE
                await self.messenger.send(
                    chat_id,
                    texts.ASK_PLACE.format(title=upload.title),
                    keyboards.place_picker(upload.id, self.graph),
                )
            else:
                upload.place_id = decision.place_id
                await self.after_place(session, upload)

    async def after_place(self, session: AsyncSession, upload: Upload) -> None:
        """Місце відоме → схожий документ того самого місця? Питаємо (D-37); ні — індексуємо."""
        if upload.place_id is None or upload.title is None:
            raise jobs.PermanentJobError(f"чернетка #{upload.id} без місця чи назви")
        candidates = await documents.replacement_candidates(session, upload.place_id, upload.title)
        if not candidates:
            await self.start_index(session, upload)
            return
        upload.status = UploadStatus.NEEDS_CHOICE
        upload.candidate_ids = [c.id for c in candidates]
        described = [await self.describe(session, c) for c in candidates]
        await self.messenger.send(
            upload.chat_id,
            texts.ASK_REPLACE.format(title=upload.title, candidate=" або ".join(described)),
            keyboards.replace_choice(upload.id, [(c.id, c.title) for c in candidates]),
        )

    async def start_index(self, session: AsyncSession, upload: Upload) -> None:
        upload.status = UploadStatus.INDEXING
        await jobs.enqueue(session, INDEX, {"upload_id": upload.id})
        self.wake()

    # --- Крок 2: індексація ---

    async def index(self, upload_id: int) -> None:
        async with self.sessions.begin() as session:
            upload = await session.get_one(Upload, upload_id, with_for_update=True)
            if upload.status is not UploadStatus.INDEXING:
                return
            if not (upload.place_id and upload.title and upload.parsed and upload.file_sha256):
                raise jobs.PermanentJobError(f"чернетка #{upload.id} неповна")
            replaces = (
                await session.get(Document, upload.replaces_id) if upload.replaces_id else None
            )
            try:
                document = await documents.create(
                    session,
                    place_id=upload.place_id,
                    title=upload.title,
                    source=DocumentSource.TELEGRAM,
                    sender_id=upload.user_id,
                    file=FileInfo(
                        file_name=upload.file_name,
                        size_bytes=upload.size_bytes,
                        file_sha256=upload.file_sha256,
                        mime=upload.mime,
                        telegram_file_id=upload.telegram_file_id,
                    ),
                    lineage_id=replaces.lineage_id if replaces else None,
                )
            except IntegrityError:
                # Той самий файл щойно додав інший звір — гонка, яку тримає БД (A-09)
                await session.rollback()
                await self._fail(upload_id, "такий самий файл уже є в боті")
                return
            previous = await index_document(
                session,
                document,
                ParsedDocument.from_json(upload.parsed),
                self.embedder,
                self.graph,
            )
            upload.status = UploadStatus.DONE
            upload.document_id = document.id
            text = texts.READY_REPLACED if previous else texts.READY
            await self.messenger.send(
                upload.chat_id,
                text.format(title=document.title, place=self.place_name(document.place_id)),
                keyboards.after_ready(upload.id),
            )
            if previous is not None:
                await self._notify_replaced(session, previous, document)
            await self._summary(session, upload)

    async def _notify_replaced(
        self, session: AsyncSession, previous: Document, document: Document
    ) -> None:
        """Тому, хто надіслав попередній варіант: що замінено, ким і коли (D-42)."""
        if previous.sender_id is None or previous.sender_id == document.sender_id:
            return
        uploader = await session.get(User, document.sender_id) if document.sender_id else None
        await self.messenger.send(
            previous.sender_id,
            texts.REPLACED_NOTICE.format(
                title=previous.title,
                place=self.place_name(previous.place_id),
                who=uploader.real_name if uploader and uploader.real_name else "звіра",
                when=f"{document.sent_at:%d.%m}",
            ),
        )

    async def _fail(self, upload_id: int, reason: str) -> None:
        async with self.sessions.begin() as session:
            upload = await session.get_one(Upload, upload_id, with_for_update=True)
            upload.status = UploadStatus.FAILED
            upload.error = reason
            await self.messenger.send(
                upload.chat_id, texts.FAILED.format(name=upload.file_name, reason=reason)
            )
            await self._summary(session, upload)

    async def _summary(self, session: AsyncSession, upload: Upload) -> None:
        """Кілька файлів одним повідомленням → наприкінці підсумок (D-53)."""
        await session.flush()
        batch = await upload_repo.finished_batch(session, upload)
        if batch is None:
            return
        ready = [u.title or u.file_name for u in batch if u.status is UploadStatus.DONE]
        failed = [u.title or u.file_name for u in batch if u.status is UploadStatus.FAILED]
        parts = []
        if ready:
            parts.append(texts.SUMMARY.format(ready=texts.quoted_list(ready)))
        if failed:
            parts.append(texts.SUMMARY_FAILED.format(failed=texts.quoted_list(failed)))
        if parts:
            await self.messenger.send(upload.chat_id, " ".join(parts))
