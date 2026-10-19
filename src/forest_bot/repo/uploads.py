"""Чернетки завантажень: стан діалогу з автором документа (D-34, D-37, D-53)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Upload, UploadStatus

# Ще в роботі: підсумок партії (D-53) — лише коли таких не лишилось
ACTIVE = (
    UploadStatus.NEEDS_NAME,
    UploadStatus.QUEUED,
    UploadStatus.NEEDS_PLACE,
    UploadStatus.NEEDS_CHOICE,
    UploadStatus.INDEXING,
)


@dataclass(frozen=True)
class IncomingFile:
    telegram_file_id: str
    file_name: str
    size_bytes: int
    mime: str | None


async def create(
    session: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    file: IncomingFile,
    status: UploadStatus,
    batch: str | None = None,
) -> Upload:
    upload = Upload(
        user_id=user_id,
        chat_id=chat_id,
        batch=batch,
        telegram_file_id=file.telegram_file_id,
        file_name=file.file_name,
        size_bytes=file.size_bytes,
        mime=file.mime,
        status=status,
    )
    session.add(upload)
    await session.flush()
    return upload


async def get_own(session: AsyncSession, upload_id: int, user_id: int) -> Upload | None:
    """Чернетка лише свого автора: чужу кнопку натиснути не вийде."""
    upload = await session.get(Upload, upload_id, with_for_update=True)
    return upload if upload is not None and upload.user_id == user_id else None


async def with_status(session: AsyncSession, user_id: int, status: UploadStatus) -> list[Upload]:
    rows = await session.scalars(
        select(Upload).where(Upload.user_id == user_id, Upload.status == status).order_by(Upload.id)
    )
    return list(rows)


async def recent_count(session: AsyncSession, user_id: int, hours: int = 1) -> int:
    """Скільки документів звір надіслав за останню годину (D-19)."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    count = await session.scalar(
        select(func.count())
        .select_from(Upload)
        .where(
            Upload.user_id == user_id,
            Upload.created_at >= since,
            Upload.status != UploadStatus.CANCELLED,
        )
    )
    return count or 0


async def finished_batch(session: AsyncSession, upload: Upload) -> list[Upload] | None:
    """Уся партія, якщо в ній кілька файлів і жоден більше не в роботі; інакше None."""
    if upload.batch is None:
        return None
    rows = list(
        await session.scalars(
            select(Upload)
            .where(Upload.user_id == upload.user_id, Upload.batch == upload.batch)
            .order_by(Upload.id)
        )
    )
    if len(rows) < 2 or any(u.status in ACTIVE for u in rows):
        return None
    return rows
