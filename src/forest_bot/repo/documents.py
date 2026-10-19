"""Документи й лінії версій: новий варіант, заміна, видалення, відкат.

Правила:
- новий варіант заміняє старий лише після підтвердження й тільки коли проіндексований (R-15, D-37);
- чинна версія лінії — одна (тримає БД);
- видалити можна лише свій документ, архівний — ні (D-05);
- видалили чинну версію → знову чинна попередня (D-43); історія лишається (D-13).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Document, DocumentSource, DocumentStatus
from forest_bot.places import PlaceGraph, normalize_name

# Схожість назв (pg_trgm) для кандидатів на заміну; уточнюється на прикладах інжесту
SIMILAR_TITLE = 0.4


class DocumentError(Exception):
    """Операцію з документом не дозволено; повідомлення — для звіра."""


@dataclass(frozen=True)
class FileInfo:
    file_name: str
    size_bytes: int
    file_sha256: str
    mime: str | None = None
    storage_path: str | None = None
    telegram_file_id: str | None = None
    archive_file_id: str | None = None
    archive_modified: str | None = None


def title_key(title: str) -> str:
    """Ключ назви: без регістру, лапок, розділових знаків і зайвих пробілів."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", normalize_name(title))).strip()


async def find_by_sha(session: AsyncSession, sha256: str) -> Document | None:
    """Той самий файл уже є (A-09)."""
    return await session.scalar(
        select(Document).where(Document.file_sha256 == sha256, Document.deleted_at.is_(None))
    )


async def replacement_candidates(
    session: AsyncSession, place_id: str, title: str, limit: int = 3
) -> list[Document]:
    """Чинні документи того самого місця з такою самою чи схожою назвою (D-37)."""
    key = title_key(title)
    similarity = func.similarity(Document.title_key, key)
    rows = await session.scalars(
        select(Document)
        .where(
            Document.is_current,
            Document.place_id == place_id,
            or_(Document.title_key == key, similarity >= SIMILAR_TITLE),
        )
        .order_by(similarity.desc(), Document.sent_at.desc())
        .limit(limit)
    )
    return list(rows)


async def create(
    session: AsyncSession,
    *,
    place_id: str,
    title: str,
    source: DocumentSource,
    file: FileInfo,
    sender_id: int | None = None,
    lineage_id: uuid.UUID | None = None,
) -> Document:
    """Нова версія: у новій лінії або в заданій (заміна). Чинною стане після activate."""
    if lineage_id is None:
        lineage_id, version = uuid.uuid4(), 1
    else:
        latest = await session.scalar(
            select(func.max(Document.version)).where(Document.lineage_id == lineage_id)
        )
        version = (latest or 0) + 1
    document = Document(
        lineage_id=lineage_id,
        version=version,
        place_id=place_id,
        title=title,
        title_key=title_key(title),
        source=source,
        sender_id=sender_id,
        file_name=file.file_name,
        size_bytes=file.size_bytes,
        file_sha256=file.file_sha256,
        mime=file.mime,
        storage_path=file.storage_path,
        telegram_file_id=file.telegram_file_id,
        archive_file_id=file.archive_file_id,
        archive_modified=file.archive_modified,
    )
    session.add(document)
    await session.flush()
    return document


async def _lock_lineage(session: AsyncSession, lineage_id: uuid.UUID) -> list[Document]:
    # Блокуємо всю лінію: дві одночасні заміни не зламають «одна чинна версія»
    rows = await session.scalars(
        select(Document)
        .where(Document.lineage_id == lineage_id)
        .order_by(Document.version)
        .with_for_update()
    )
    return list(rows)


async def activate(session: AsyncSession, document: Document) -> Document | None:
    """Документ проіндексовано → він чинний; повертає замінену версію (для D-42)."""
    lineage = await _lock_lineage(session, document.lineage_id)
    previous = next((d for d in lineage if d.is_current and d.id != document.id), None)
    await session.execute(
        update(Document)
        .where(Document.lineage_id == document.lineage_id, Document.is_current)
        .values(is_current=False)
    )
    # Спершу зняти стару позначку, потім поставити нову — інакше спрацює unique index
    await session.flush()
    document.status = DocumentStatus.READY
    document.error = None
    document.is_current = True
    await session.flush()
    return previous


async def mark_failed(session: AsyncSession, document: Document, error: str) -> None:
    document.status = DocumentStatus.FAILED
    document.error = error
    await session.flush()


async def delete(session: AsyncSession, document_id: int, user_id: int) -> Document | None:
    """Видаляє свій документ; якщо він був чинним — повертає версію, що знову діє (D-43)."""
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise DocumentError("Такого документа немає")
    if document.source is DocumentSource.ARCHIVE:
        raise DocumentError("Документи з «Лісового архіву» через бота не видаляються")
    if document.sender_id != user_id:
        raise DocumentError("Видалити можна лише свій документ")

    lineage = await _lock_lineage(session, document.lineage_id)
    was_current = document.is_current
    document.is_current = False
    document.deleted_at = datetime.now(UTC)
    await session.flush()
    if not was_current:
        return None
    previous = next(
        (
            d
            for d in reversed(lineage)
            if d.id != document.id and d.status is DocumentStatus.READY and d.deleted_at is None
        ),
        None,
    )
    if previous is not None:
        previous.is_current = True
        await session.flush()
    return previous


async def archive_version_changed(session: AsyncSession, archive_file_id: str, sha256: str) -> bool:
    """Чи змінився файл архіву порівняно з останньою архівною версією.

    Порівнюємо з архівною версією, а не з чинною: якщо документ замінили з Telegram,
    незмінний файл на Диску не повинен відкотити заміну (R-15).
    """
    latest_sha = await session.scalar(
        select(Document.file_sha256)
        .where(Document.archive_file_id == archive_file_id)
        .order_by(Document.version.desc())
        .limit(1)
    )
    return latest_sha != sha256


async def current_for_place(
    session: AsyncSession, graph: PlaceGraph, place_id: str
) -> list[Document]:
    """Чинні документи, доречні для місця: його власні, частин і лісу (D-25)."""
    applicable = graph.applicable(place_id)
    level = {a.place.id: a.level for a in applicable}
    rows = await session.scalars(
        select(Document).where(Document.is_current, Document.place_id.in_(level))
    )
    return sorted(rows, key=lambda d: (level[d.place_id], d.title_key))


async def of_user(session: AsyncSession, user_id: int) -> list[Document]:
    """«Мої документи» (D-05): усі невидалені документи звіра, новіші першими."""
    rows = await session.scalars(
        select(Document)
        .where(Document.sender_id == user_id, Document.deleted_at.is_(None))
        .order_by(Document.sent_at.desc(), Document.id.desc())
    )
    return list(rows)
