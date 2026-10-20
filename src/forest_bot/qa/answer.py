"""Питання → відповідь (S-06): місце → пошук → LLM → текст із підписами → журнал.

Модель лише формулює частини відповіді й каже, з якого фрагмента кожна. Що шукати
(область місця, чинні версії), у якому порядку показати (R-24) і як підписати (R-12,
D-18) — вирішує код.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from forest_bot.llm.client import Completion, GroqClient
from forest_bot.llm.prompts import Answer, Fragment, answer_messages
from forest_bot.models import QaLog, Unanswered
from forest_bot.places import Level, Place, PlaceGraph, PlaceKind
from forest_bot.qa.places import detect_places, find_places, focus
from forest_bot.qa.render import RenderedPart, compose, order, short_title
from forest_bot.qa.search import Passage, scope, search

logger = logging.getLogger(__name__)


def relation(passage_place: Place, asked: Place) -> str:
    """Рівень фрагмента відносно місця з питання. Без цього модель відкидає пораду листяного
    лісу для рукавички «Під вербою»: «там же про дуби, а не про верби»."""
    if passage_place.id == asked.id:
        return "саме це місце"
    if passage_place.level is Level.PART:
        return "частина лісу, куди входить це місце"
    return "весь ліс — діє для кожного місця"


ANSWER_MAX_TOKENS = 500

NOT_FOUND = (
    "Не знайшов поради про це для {place}. Якщо така порада є в документі — надішли його мені, "
    "і звірі зможуть її знайти."
)
ONLY_GENERAL = "Окремої поради для {place} немає — ось загальніша."
FOR_PLACE = "Для {place}:"
SHARED = "Спільне для {places}:"
NOT_FOUND_SHORT = "не знайшов поради про це."


class QueryEmbedder(Protocol):
    """Що потрібно від моделі embeddings: вектор питання (`query: …`)."""

    async def aembed_query(self, text: str) -> npt.NDArray[np.float32]: ...


@dataclass(frozen=True)
class Source:
    """Документ-джерело для кнопки «📄 …» під відповіддю (D-28)."""

    document_id: int
    title: str


@dataclass(frozen=True)
class Reply:
    text: str
    found: bool
    place_ids: tuple[str, ...]
    sources: tuple[Source, ...] = ()
    qa_id: int | None = None
    # Місце не назване й не з'ясоване — бот питає кнопками (D-06)
    needs_place: bool = False
    # Документи, які знайшов пошук (від найближчого), — для eval: hit@k
    retrieved: tuple[int, ...] = ()


@dataclass
class _PlaceAnswer:
    place: Place
    parts: list[RenderedPart]
    differs: bool


def place_label(place: Place, graph: PlaceGraph) -> str:
    """Як місце назвати моделі: «рукавичка «Під дубом»», «частина лісу: Хвойний ліс».

    Рукавичка у двох частинах лісу — так і кажемо: поради обох частин для неї діють
    і можуть різнитися (D-03). Інакше модель лишає одну, «ближчу» за словами.
    """
    if place.kind is PlaceKind.MITTEN:
        label = f"рукавичка «{place.name}»"
        if len(place.parts) > 1:
            parts = " і ".join(graph.get(p).citation for p in place.parts)
            label += (
                f"; входить у дві частини лісу — {parts}: поради обох для неї діють, "
                "дай кожну окремою частиною"
            )
        return label
    if place.kind is PlaceKind.PART:
        return f"частина лісу: {place.name}"
    return "увесь ліс"


class Answerer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embedder: QueryEmbedder,
        graph: PlaceGraph,
        llm: GroqClient,
        timezone: ZoneInfo,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        self.embedder = embedder
        self.graph = graph
        self.llm = llm
        self.timezone = timezone
        self.clock = clock

    async def ask(
        self,
        question: str,
        place_ids: Sequence[str] | None = None,
        *,
        previous: str = "",
        on_wait: Callable[[float], Awaitable[None]] | None = None,
    ) -> Reply:
        """Відповідь на питання; `place_ids` — коли місце вже обрано кнопкою чи з контексту.

        `previous` — попереднє питання, коли це уточнення без місця (D-24): «а до якого
        числа?» саме нічого не знайде, тож шукаємо за обома, а моделі даємо обидва окремо.
        """
        started = time.monotonic()
        if place_ids is None:
            found = await detect_places(question, self.graph, self.llm)
            if not found.place_ids:
                return Reply("", found=False, place_ids=(), needs_place=True)
            place_ids = found.place_ids
        else:
            found = find_places(question, self.graph)
        searched = f"{previous} {question}" if previous else question
        rest = find_places(searched, self.graph).rest
        # Питання цілком і без назв місць — див. qa/search.py
        vectors = [(await self.embedder.aembed_query(searched)).tolist()]
        if rest and rest != searched:
            vectors.append((await self.embedder.aembed_query(rest)).tolist())

        completions: list[Completion] = []
        answers: list[_PlaceAnswer] = []
        retrieved: list[int] = []
        async with self.sessions.begin() as session:
            for place_id in place_ids:
                place = self.graph.get(place_id)
                passages = await search(
                    session, vectors, rest, scope(place_id, self.graph), self.graph
                )
                retrieved.extend(p.document_id for p in passages)
                asked = focus(question, found, place_id)
                answers.append(
                    await self._answer(asked, previous, place, passages, completions, on_wait)
                )
            text = self._compose(answers)
            # У порядку показу: конкретна порада першою (R-24) — так і кнопки джерел
            parts = [part for answer in answers for part in order(answer.parts)]
            answered = bool(parts)
            for answer in answers:
                if not answer.parts:
                    # D-36: без того, хто питав
                    session.add(Unanswered(question=question, place_id=answer.place.id))
            log = QaLog(
                question=question,
                place_ids=list(place_ids),
                chunk_ids=list(dict.fromkeys(c for p in parts for c in p.passage.chunk_ids)),
                answer=text,
                found=answered,
                model=completions[-1].model if completions else None,
                prompt_tokens=sum(c.prompt_tokens for c in completions) or None,
                completion_tokens=sum(c.completion_tokens for c in completions) or None,
                latency_ms=round((time.monotonic() - started) * 1000),
            )
            session.add(log)
            await session.flush()
            qa_id = log.id
        cited = {p.passage.document_id: p.passage for p in parts}
        short = {i: short_title(p.title, self.graph.get(p.place_id)) for i, p in cited.items()}
        repeated = {t for t in short.values() if list(short.values()).count(t) > 1}
        # Дві кнопки «📄 Членські внески» для двох місць не розрізнити — додаємо місце
        sources = {
            i: Source(
                i,
                f"{short[i]} · {self.graph.get(p.place_id).name}"
                if short[i] in repeated
                else short[i],
            )
            for i, p in cited.items()
        }
        logger.info(
            "Питання → %s: %s, джерел %d, %s, токенів %s+%s, %d мс",
            ", ".join(place_ids),
            "знайшов" if answered else "не знайшов",
            len(sources),
            log.model,
            log.prompt_tokens,
            log.completion_tokens,
            log.latency_ms,
        )
        return Reply(
            text,
            answered,
            tuple(place_ids),
            tuple(sources.values()),
            qa_id,
            retrieved=tuple(dict.fromkeys(retrieved)),
        )

    async def _answer(
        self,
        question: str,
        previous: str,
        place: Place,
        passages: list[Passage],
        completions: list[Completion],
        on_wait: Callable[[float], Awaitable[None]] | None,
    ) -> _PlaceAnswer:
        if not passages:
            return _PlaceAnswer(place, [], differs=False)
        fragments = [
            Fragment(
                id=index,
                text=passage.text,
                document=passage.title,
                place=self.graph.get(passage.place_id).citation,
                level=relation(self.graph.get(passage.place_id), place),
            )
            for index, passage in enumerate(passages, start=1)
        ]
        today = self.clock().astimezone(self.timezone).date()
        answer, completion = await self.llm.complete_json(
            answer_messages(question, fragments, today, place_label(place, self.graph), previous),
            Answer,
            max_tokens=ANSWER_MAX_TOKENS,
            temperature=0.1,
            on_wait=on_wait,
        )
        completions.append(completion)
        return _PlaceAnswer(place, self._parts(answer, passages), answer.differs)

    @staticmethod
    def _parts(answer: Answer, passages: list[Passage]) -> list[RenderedPart]:
        """Лише частини з наданих фрагментів (ADR-05): вигадане джерело відкидається."""
        if not answer.found:
            return []
        parts: list[RenderedPart] = []
        used: set[int] = set()
        for part in answer.parts:
            if 1 <= part.source <= len(passages) and part.text.strip() and part.source not in used:
                used.add(part.source)
                parts.append(RenderedPart(part.text, passages[part.source - 1]))
        return parts

    def _compose(self, answers: list[_PlaceAnswer]) -> str:
        if len(answers) == 1:
            return self._one(answers[0])
        # D-35: спільна для кількох місць порада (той самий документ) — один раз, наприкінці
        seen: dict[int, int] = {}
        for answer in answers:
            for document_id in {p.passage.document_id for p in answer.parts}:
                seen[document_id] = seen.get(document_id, 0) + 1
        shared: dict[int, RenderedPart] = {}
        blocks = []
        for answer in answers:
            own = [p for p in answer.parts if seen[p.passage.document_id] == 1]
            for part in answer.parts:
                if seen[part.passage.document_id] > 1:
                    shared.setdefault(part.passage.document_id, part)
            header = FOR_PLACE.format(place=answer.place.citation)
            if own:
                body = compose(own, self.graph, self.timezone, differs=answer.differs)
                blocks.append(f"{header}\n{body}")
            elif not answer.parts:
                blocks.append(f"{header} {NOT_FOUND_SHORT}")
        if shared:
            places = " і ".join(a.place.citation for a in answers if a.parts)
            common = compose(list(shared.values()), self.graph, self.timezone, differs=False)
            blocks.append(f"{SHARED.format(places=places)}\n{common}")
        return "\n\n".join(blocks)

    def _one(self, answer: _PlaceAnswer) -> str:
        if not answer.parts:
            return NOT_FOUND.format(place=answer.place.citation)
        text = compose(answer.parts, self.graph, self.timezone, differs=answer.differs)
        # D-55: місце питання назване, навіть якщо порада лише загальніша
        if answer.place.kind is not PlaceKind.FOREST and all(
            p.passage.level > answer.place.level for p in answer.parts
        ):
            return f"{ONLY_GENERAL.format(place=answer.place.citation)}\n\n{text}"
        return text
