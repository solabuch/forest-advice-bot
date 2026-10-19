"""Черга задач: виконання, повтори з паузою, остаточна помилка, відновлення після рестарту."""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot import jobs
from forest_bot.jobs import MAX_ATTEMPTS, PermanentJobError, Worker
from forest_bot.logs import log_context
from forest_bot.models import Job, JobStatus

pytestmark = pytest.mark.integration

Sessions = async_sessionmaker[AsyncSession]


async def add(sessions: Sessions, kind: str = "echo", **payload: Any) -> int:
    async with sessions.begin() as session:
        return (await jobs.enqueue(session, kind, payload)).id


async def load(sessions: Sessions, job_id: int) -> Job:
    async with sessions() as session:
        return await session.get_one(Job, job_id)


async def test_job_runs_once_and_is_done(sessions: Sessions) -> None:
    seen: list[dict[str, Any]] = []
    labels: list[str] = []

    async def echo(payload: dict[str, Any]) -> None:
        seen.append(payload)
        labels.append(log_context.get())

    job_id = await add(sessions, document_id=7)
    worker = Worker(sessions, {"echo": echo})
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert seen == [{"document_id": 7}]
    # Усі записи задачі в лозі — з її номером
    assert labels == [f"job {job_id} echo"]
    job = await load(sessions, job_id)
    assert (job.status, job.attempts, job.locked_at) == (JobStatus.DONE, 1, None)


async def test_failure_is_retried_later(sessions: Sessions) -> None:
    async def flaky(_: dict[str, Any]) -> None:
        raise ConnectionError("Telegram недоступний")

    job_id = await add(sessions)
    await Worker(sessions, {"echo": flaky}).run_once()
    job = await load(sessions, job_id)
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 1)
    assert job.run_after > datetime.now(UTC)
    assert job.last_error is not None
    assert "Telegram недоступний" in job.last_error
    # Пауза ще не минула — задачу не беруть
    assert await Worker(sessions, {"echo": flaky}).run_once() is False


async def test_attempts_are_limited(sessions: Sessions) -> None:
    async def broken(_: dict[str, Any]) -> None:
        raise RuntimeError("знову")

    job_id = await add(sessions)
    async with sessions.begin() as session:
        job = await session.get_one(Job, job_id)
        job.attempts = MAX_ATTEMPTS - 1
    await Worker(sessions, {"echo": broken}).run_once()
    assert (await load(sessions, job_id)).status is JobStatus.FAILED


@pytest.mark.parametrize("kind", ["echo", "unknown"])
async def test_permanent_error_fails_at_once(sessions: Sessions, kind: str) -> None:
    async def bad_file(_: dict[str, Any]) -> None:
        raise PermanentJobError("формат не підтримується")

    job_id = await add(sessions, kind)
    await Worker(sessions, {"echo": bad_file}).run_once()
    job = await load(sessions, job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 1)


async def test_running_jobs_are_recovered_after_restart(sessions: Sessions) -> None:
    job_id = await add(sessions)
    async with sessions.begin() as session:
        job = await session.get_one(Job, job_id)
        job.status, job.locked_at = JobStatus.RUNNING, datetime.now(UTC)
    assert await Worker(sessions, {}).recover() >= 1
    assert (await load(sessions, job_id)).status is JobStatus.QUEUED


async def test_has_pending(sessions: Sessions) -> None:
    async with sessions.begin() as session:
        assert not await jobs.has_pending(session, "archive_import")
        await jobs.enqueue(session, "archive_import")
        assert await jobs.has_pending(session, "archive_import")
        done = await session.scalar(select(Job).where(Job.kind == "archive_import"))
        assert done is not None
        done.status = JobStatus.DONE
        await session.flush()
        assert not await jobs.has_pending(session, "archive_import")
