"""Гібридний пошук (ADR-05): область місця в SQL → вектори + слова → RRF → уривки документів.

Фрагмент з чужого місця чи замінений варіант не може потрапити у відповідь: фільтр — у
запиті, а не в промпті. Документи архіву короткі (≈ сторінка), тож у відповідь іде весь
документ (small-to-big): «Хто не платить» поруч із «Основне», навіть якщо знайшовся один
фрагмент. Великий документ — знайдені фрагменти із сусідами.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.models import Chunk, Document, DocumentSource, User
from forest_bot.places import Level, PlaceGraph
from forest_bot.qa.places import normalize, stem

# Reciprocal Rank Fusion: стандартна стала зі статті Cormack et al.
RRF_K = 60
CANDIDATES = 30
MAX_DOCUMENTS = 4
# Усі уривки разом: ~1400 токенів. Ліміт Groq — 8000 токенів на хвилину на модель,
# тож довший контекст — менше відповідей за хвилину (D-23)
CONTEXT_CHARS = 4000
# Документ, коротший за це, іде у відповідь цілком (≈ 700 токенів)
WHOLE_DOCUMENT_CHARS = 2800
# Нижче — фрагмент не про це; але збіг слів («Кабан», «4 жолуді») рятує і з нижчим
MIN_COSINE = 0.80

# Слова питання, які нічого не шукають: є майже в кожному питанні чи документі
_STOPWORDS = {
    *("як", "що", "хто", "де", "коли", "скільки", "чи", "який", "яка", "яке", "які", "якого"),
    *("яких", "робити", "зробити", "можу", "може", "можемо"),
    *("кому", "кого", "чому", "навіщо", "куди", "звідки", "можна", "треба", "потрібно"),
    *("мені", "нам", "нас", "ми", "ти", "ви", "він", "вона", "вони", "це", "цей", "ця", "ці"),
    *("той", "там", "тут", "був", "була", "буде", "бути", "має", "мають", "для", "від"),
    *("про", "при", "без", "через", "після", "перед", "між", "або", "але", "так", "ще"),
    *("вже", "лише", "тільки", "теж", "також", "дуже", "усе", "все", "всі", "усі", "кожен"),
    *("наш", "наша", "наше", "наші", "нашої", "нашого", "свій", "своє", "підкажи", "скажи"),
    *("розкажи", "порадь", "будь", "ласка", "привіт", "дякую", "сьогодні", "завтра"),
}
_NOISE_PREFIXES = ("рукавич", "ліс", "мешканц")
_TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


@dataclass(frozen=True)
class Passage:
    """Уривок документа для відповіді: текст і все, що треба для підпису (D-18, R-12)."""

    document_id: int
    title: str
    place_id: str
    level: Level
    source: DocumentSource
    sender: str | None
    sent_at: datetime
    whole: bool
    sections: tuple[str, ...]
    pages: tuple[int, ...]
    text: str
    chunk_ids: tuple[int, ...]
    cosine: float
    keyword: bool


def scope(place_id: str, graph: PlaceGraph) -> list[str]:
    """Доречні місця (R-10, R-17, R-27): рукавичка → вона, її частини, ліс; частина →
    вона й ліс; ліс → лише ліс."""
    return [a.place.id for a in graph.applicable(place_id)]


def keyword_query(question: str) -> str | None:
    """Слова питання → tsquery з префіксами: «жолудів» знайде «жолуді», «внесок» — «внески».

    Словник `simple` без стемінгу (документи бувають будь-якою мовою), тож відмінки
    закриває префікс: основа без закінчення, але не коротша за 3–4 літери.
    """
    terms: list[str] = []
    for raw in _TOKEN.findall(normalize(question)):
        if raw in _STOPWORDS:
            continue
        # Парсер Postgres рве слово на апострофі: «п'ятницю» в індексі — «п» і «ятницю»
        token = max(raw.split("'"), key=len)
        if token in _STOPWORDS or token.startswith(_NOISE_PREFIXES):
            continue
        if token.isdigit():
            terms.append(token)
            continue
        if len(token) < 3:
            continue
        # Не довше 5 літер: дієслова міняють суфікс — «утеплювати» / «утеплюють» / «утепліть»
        floor = 3 if len(token) <= 5 else 4
        prefix = token[: max(floor, min(len(stem(token)), len(token) - 2, 5))]
        terms.append(f"{prefix}:*")
    unique = list(dict.fromkeys(terms))
    return " | ".join(unique) if unique else None


def _rrf(*rankings: Sequence[int]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)
    return scores


async def search(
    session: AsyncSession,
    vectors: Sequence[Sequence[float]],
    question: str,
    place_ids: Sequence[str],
    graph: PlaceGraph,
    *,
    max_documents: int = MAX_DOCUMENTS,
) -> list[Passage]:
    """Уривки чинних документів доречних місць, від найближчого до питання.

    `vectors` — питання цілком і без назв місць: перше тримає документи самого місця,
    друге — тему, тож загальна порада лісу про те саме теж має шанс (R-24).
    """
    in_scope = (Document.is_current.is_(True), Document.place_id.in_(place_ids))
    # HNSW із фільтром інакше може віддати менше за LIMIT (pgvector ≥ 0.8)
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    dense_rankings: list[list[int]] = []
    cosine: dict[int, float] = {}
    for vector in vectors:
        distance = Chunk.embedding.cosine_distance(vector)
        dense = (
            await session.execute(
                select(Chunk.id, distance)
                .join(Document, Document.id == Chunk.document_id)
                .where(*in_scope)
                .order_by(distance)
                .limit(CANDIDATES)
            )
        ).all()
        dense_rankings.append([chunk_id for chunk_id, _ in dense])
        for chunk_id, d in dense:
            cosine[chunk_id] = max(cosine.get(chunk_id, 0.0), 1 - float(d))

    matched: list[int] = []
    if tsquery := keyword_query(question):
        query = func.to_tsquery("simple", tsquery)
        # Слова назви й розділу теж шукаються: «Небезпеки», «Статут» часто є лише в назві.
        # Без індексу — області місця кілька десятків фрагментів
        words = Chunk.tsv.op("||")(
            func.to_tsvector("simple", func.concat_ws(" ", Document.title, Chunk.section))
        )
        rank = func.ts_rank_cd(words, query)
        matched = list(
            await session.scalars(
                select(Chunk.id)
                .join(Document, Document.id == Chunk.document_id)
                .where(*in_scope, words.op("@@")(query))
                .order_by(rank.desc(), Chunk.id)
                .limit(CANDIDATES)
            )
        )
    keyword = set(matched)
    relevant = {c for c, score in cosine.items() if score >= MIN_COSINE} | keyword
    scores = _rrf(*([c for c in r if c in relevant] for r in dense_rankings), matched)
    if not scores:
        return []

    hits = (
        await session.execute(select(Chunk.id, Chunk.document_id).where(Chunk.id.in_(scores)))
    ).all()
    by_document: dict[int, list[int]] = {}
    for chunk_id, document_id in hits:
        by_document.setdefault(document_id, []).append(chunk_id)
    ranked = sorted(by_document, key=lambda d: -max(scores[c] for c in by_document[d]))
    passages: list[Passage] = []
    size = 0
    for document_id in ranked[:max_documents]:
        passage = await _passage(
            session, document_id, by_document[document_id], cosine, keyword, graph
        )
        if passages and size + len(passage.text) > CONTEXT_CHARS:
            break
        passages.append(passage)
        size += len(passage.text)
    return passages


async def _passage(
    session: AsyncSession,
    document_id: int,
    hit_ids: list[int],
    cosine: dict[int, float],
    keyword: set[int],
    graph: PlaceGraph,
) -> Passage:
    row = (
        await session.execute(
            select(Document, User.real_name)
            .outerjoin(User, User.id == Document.sender_id)
            .where(Document.id == document_id)
        )
    ).one()
    document, sender = row
    chunks = list(
        await session.scalars(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ord)
        )
    )
    whole = sum(len(c.text) for c in chunks) <= WHOLE_DOCUMENT_CHARS
    if not whole:
        # Великий документ: знайдені фрагменти й по сусіду з кожного боку
        hit_ords = {c.ord for c in chunks if c.id in hit_ids}
        near = {o + shift for o in hit_ords for shift in (-1, 0, 1)}
        chunks = [c for c in chunks if c.ord in near]
    return Passage(
        document_id=document.id,
        title=document.title,
        place_id=document.place_id,
        level=graph.get(document.place_id).level,
        source=document.source,
        sender=sender,
        sent_at=document.sent_at,
        whole=whole,
        sections=tuple(dict.fromkeys(c.section for c in chunks if c.section)),
        pages=tuple(sorted({c.page for c in chunks if c.page})),
        text="\n\n".join(c.text for c in chunks),
        chunk_ids=tuple(c.id for c in chunks),
        cosine=max((cosine.get(c, 0.0) for c in hit_ids), default=0.0),
        keyword=any(c in keyword for c in hit_ids),
    )
