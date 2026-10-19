"""Індексація: фрагменти з векторами в БД і заміна чинної версії — в одній транзакції.

Стара версія перестає діяти лише тоді, коли нова вже знаходиться пошуком (R-15):
якщо обробка впала посередині, транзакція відкочується, і далі діє стара.
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
import numpy.typing as npt
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.ingest.chunk import chunk, context_header
from forest_bot.ingest.parse import ParsedDocument
from forest_bot.models import Chunk, Document
from forest_bot.places import PlaceGraph
from forest_bot.repo import documents


class PassageEmbedder(Protocol):
    def count_tokens(self, text: str) -> int: ...

    async def aembed_passages(self, texts: Sequence[str]) -> npt.NDArray[np.float32]: ...


class NothingToIndexError(Exception):
    """Документ не дав жодного фрагмента."""


async def index_document(
    session: AsyncSession,
    document: Document,
    parsed: ParsedDocument,
    embedder: PassageEmbedder,
    graph: PlaceGraph,
) -> Document | None:
    """Фрагменти → вектори → БД → документ чинний; повертає замінену версію (D-42)."""
    pieces = chunk(parsed, embedder.count_tokens)
    if not pieces:
        raise NothingToIndexError("у документі немає тексту для пошуку")
    place = graph.get(document.place_id).citation
    vectors = await embedder.aembed_passages(
        [f"{context_header(document.title, place, p.section)}\n{p.text}" for p in pieces]
    )
    # Повторна індексація (рестарт посеред обробки) — без дублів
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    session.add_all(
        Chunk(
            document_id=document.id,
            ord=index,
            text=piece.text,
            section=piece.section[:300] if piece.section else None,
            page=piece.page,
            embedding=vector.tolist(),
        )
        for index, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
    )
    await session.flush()
    return await documents.activate(session, document)
