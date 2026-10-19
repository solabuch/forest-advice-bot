"""Питання → відповідь (S-06) на справжній БД з фейковим Groq: підписи, вигадані джерела,
«не знайшов» (D-08, D-36), загальніша порада (D-55), кілька місць (D-35), журнал."""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import numpy.typing as npt
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot.llm.client import GroqClient
from forest_bot.models import QaLog, Unanswered
from forest_bot.places import load_places
from forest_bot.qa.answer import NOT_FOUND, ONLY_GENERAL, Answerer, Source, place_label
from forest_bot.repo.places import sync_places
from tests.factories import FAKE_GROQ_KEY
from tests.integration.test_search import QUESTION, add

pytestmark = pytest.mark.integration

GRAPH = load_places()
Sessions = async_sessionmaker[AsyncSession]
_FRAGMENT = re.compile(r'<фрагмент id="(\d+)" документ="([^"]+)"')


class QueryVectors:
    async def aembed_query(self, text: str) -> npt.NDArray[np.float32]:
        return np.array(QUESTION, dtype=np.float32)


def every_fragment(user: str) -> dict[str, Any]:
    """Модель, що відповідає з кожного фрагмента: «Порада з <документ>.»"""
    parts = [{"source": int(i), "text": f"Порада з {doc}."} for i, doc in _FRAGMENT.findall(user)]
    return {"found": bool(parts), "parts": parts, "differs": False}


class FakeGroq:
    def __init__(self, answer: Callable[[str], dict[str, Any]] = every_fragment) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        self.prompts.append(user)
        body = {
            "choices": [
                {"message": {"content": json.dumps(self.answer(user))}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
        return httpx.Response(200, json=body)

    def client(self) -> GroqClient:
        return GroqClient(
            FAKE_GROQ_KEY, ("vendor/test",), transport=httpx.MockTransport(self.handle)
        )


@pytest.fixture
async def forest(sessions: Sessions) -> Sessions:
    async with sessions.begin() as session:
        await sync_places(session, GRAPH)
    return sessions


def answerer(sessions: Sessions, groq: FakeGroq) -> Answerer:
    return Answerer(
        sessions,
        QueryVectors(),
        GRAPH,
        groq.client(),
        ZoneInfo("Europe/Kyiv"),
        clock=lambda: datetime(2026, 10, 19, 9, tzinfo=UTC),
    )


async def documents(sessions: Sessions, *items: tuple[str, str]) -> list[int]:
    async with sessions.begin() as session:
        return [(await add(session, place_id, title)).id for place_id, title in items]


async def test_answer_is_signed_and_logged(forest: Sessions) -> None:
    [fees] = await documents(forest, ("pid-dubom", "Членські внески рукавички «Під дубом»"))
    reply = await answerer(forest, FakeGroq()).ask("Скільки внесок під дубом?")
    assert reply.text == (
        "Порада з Членські внески рукавички «Під дубом». "
        "— з документа «Членські внески» рукавички «Під дубом»"
    )
    assert (reply.found, reply.place_ids) == (True, ("pid-dubom",))
    assert reply.sources == (Source(fees, "Членські внески"),)
    async with forest() as session:
        log = await session.get_one(QaLog, reply.qa_id)
    assert (log.found, log.place_ids, log.model) == (True, ["pid-dubom"], "vendor/test")
    assert log.chunk_ids


async def test_invented_source_is_dropped(forest: Sessions) -> None:
    await documents(forest, ("pid-dubom", "Внески"))
    groq = FakeGroq(
        lambda _: {"found": True, "parts": [{"source": 7, "text": "Х"}], "differs": False}
    )
    reply = await answerer(forest, groq).ask("Скільки внесок під дубом?")
    assert not reply.found
    assert reply.text == NOT_FOUND.format(place="рукавички «Під дубом»")


async def test_nothing_in_scope_is_not_found_without_llm(forest: Sessions) -> None:
    await documents(forest, ("u-lishchyni", "Внески"))
    groq = FakeGroq()
    reply = await answerer(forest, groq).ask("Скільки внесок під дубом?")
    assert (reply.found, groq.prompts) == (False, [])
    # D-36: питання без відповіді — без того, хто питав
    async with forest() as session:
        unanswered = await session.scalar(select(Unanswered))
    assert unanswered is not None
    assert (unanswered.question, unanswered.place_id) == ("Скільки внесок під дубом?", "pid-dubom")


async def test_only_general_advice_names_the_place(forest: Sessions) -> None:
    await documents(forest, ("deciduous", "Як утеплити рукавичку в листяному лісі"))
    reply = await answerer(forest, FakeGroq()).ask("Як утеплити рукавичку у ліщині?")
    assert reply.text.startswith(ONLY_GENERAL.format(place="рукавички «У ліщині»"))
    assert reply.text.endswith("— з документа «Як утеплити рукавичку» листяного лісу")


async def test_no_place_asks_with_buttons(forest: Sessions) -> None:
    groq = FakeGroq(lambda _: {"place_id": None, "confidence": 0.0})
    reply = await answerer(forest, groq).ask("Що їсти?")
    assert reply.needs_place
    async with forest() as session:
        assert await session.scalar(select(QaLog)) is None


async def test_several_places_each_with_shared_once(forest: Sessions) -> None:
    await documents(
        forest,
        ("pid-dubom", "Чим поживитись під дубом"),
        ("u-lishchyni", "Чим поживитись у ліщині"),
        ("deciduous", "Що їсти в листяному лісі"),
    )
    groq = FakeGroq()
    reply = await answerer(forest, groq).ask("Що їсти під дубом і в ліщині?")
    blocks = reply.text.split("\n\n")
    assert blocks[0].startswith("Для рукавички «Під дубом»:\nПорада з Чим поживитись під дубом.")
    assert blocks[1].startswith("Для рукавички «У ліщині»:\nПорада з Чим поживитись у ліщині.")
    # Порада листяного лісу діє для обох — один раз, наприкінці
    assert blocks[2].startswith("Спільне для рукавички «Під дубом» і рукавички «У ліщині»:")
    assert reply.text.count("Що їсти в листяному лісі") == 1
    # Кожне місце — окремим питанням: інакше модель відповідає не про те місце
    assert "<питання>\nЩо їсти під дубом?\n</питання>" in groq.prompts[0]
    assert "<питання>\nЩо їсти в ліщині?\n</питання>" in groq.prompts[1]


def test_mitten_in_two_parts_is_explained_to_the_model() -> None:
    label = place_label(GRAPH.get("pid-verboiu"), GRAPH)
    assert "листяного лісу і частини лісу біля річки" in label
