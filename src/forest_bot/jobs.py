"""Черга фонових задач у Postgres (ADR-06): переживає рестарт, окремого сервісу не треба.

- Задачу бере `SELECT … FOR UPDATE SKIP LOCKED`.
- Помилка → повтор із паузою 10 с · 2ⁿ; після MAX_ATTEMPTS — failed.
  `PermanentJobError` (битий файл, непідтримуваний формат) — одразу failed.
- Worker один на базу (ROLE=all чи worker): на старті задачі в стані running —
  від попереднього життя процесу, їх повертаємо в чергу.
- Обробники ідемпотентні: задача, що впала після свого коміту, але до позначки
  done, просто виконається ще раз.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot.models import Job, JobStatus

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 10.0
POLL_SECONDS = 1.0

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class PermanentJobError(Exception):
    """Повтор не допоможе: задача одразу failed."""


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    delay: float = 0.0,
) -> Job:
    job = Job(kind=kind, payload=payload or {})
    if delay:
        job.run_after = datetime.now(UTC) + timedelta(seconds=delay)
    session.add(job)
    await session.flush()
    return job


async def has_pending(session: AsyncSession, kind: str) -> bool:
    """Така задача вже чекає чи виконується — друга не потрібна."""
    found = await session.scalar(
        select(Job.id)
        .where(Job.kind == kind, Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)))
        .limit(1)
    )
    return found is not None


class Worker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        handlers: Mapping[str, Handler],
        *,
        poll: float = POLL_SECONDS,
    ) -> None:
        self._sessions = sessions
        self._handlers = dict(handlers)
        self._poll = poll
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Нова задача в цьому ж процесі — не чекати наступного опитування."""
        self._wake.set()

    async def recover(self) -> int:
        """Задачі, що «виконувались» до рестарту, — знову в черзі."""
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(Job)
                .where(Job.status == JobStatus.RUNNING)
                .values(status=JobStatus.QUEUED, locked_at=None)
            )
        recovered: int = getattr(result, "rowcount", 0) or 0
        if recovered:
            logger.info("Задач після рестарту повернуто в чергу: %d", recovered)
        return recovered

    async def run(self) -> None:
        await self.recover()
        while True:
            if not await self.run_once():
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll)

    async def run_once(self) -> bool:
        """Бере й виконує одну задачу; False — черга порожня."""
        claimed = await self._claim()
        if claimed is None:
            return False
        job_id, kind, payload, attempt = claimed
        handler = self._handlers.get(kind)
        try:
            if handler is None:
                raise PermanentJobError(f"невідомий тип задачі: {kind}")
            await handler(payload)
        except PermanentJobError as error:
            await self._finish(job_id, JobStatus.FAILED, str(error))
            logger.warning("Задача %s #%d не виконується: %s", kind, job_id, error)
        except Exception as error:
            if attempt >= MAX_ATTEMPTS:
                await self._finish(job_id, JobStatus.FAILED, repr(error))
                logger.exception("Задача %s #%d: спроби вичерпано", kind, job_id)
            else:
                delay = BACKOFF_SECONDS * 2 ** (attempt - 1)
                await self._retry(job_id, repr(error), delay)
                logger.warning("Задача %s #%d: %r — повтор за %.0f с", kind, job_id, error, delay)
        else:
            await self._finish(job_id, JobStatus.DONE, None)
        return True

    async def _claim(self) -> tuple[int, str, dict[str, Any], int] | None:
        async with self._sessions.begin() as session:
            job = await session.scalar(
                select(Job)
                .where(Job.status == JobStatus.QUEUED, Job.run_after <= datetime.now(UTC))
                .order_by(Job.run_after, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            job.status = JobStatus.RUNNING
            job.locked_at = datetime.now(UTC)
            job.attempts += 1
            return job.id, job.kind, dict(job.payload), job.attempts

    async def _finish(self, job_id: int, status: JobStatus, error: str | None) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status=status, locked_at=None, last_error=error and error[:2000])
            )

    async def _retry(self, job_id: int, error: str, delay: float) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=JobStatus.QUEUED,
                    locked_at=None,
                    last_error=error[:2000],
                    run_after=datetime.now(UTC) + timedelta(seconds=delay),
                )
            )
