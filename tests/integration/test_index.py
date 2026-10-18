"""Індексація: фрагменти з векторами, tsvector від БД, заміна версії в одній транзакції."""

import hashlib
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from forest_bot.ingest.index import NothingToIndexError, index_document
from forest_bot.ingest.parse import Block, ParsedDocument
from forest_bot.models import EMBEDDING_DIM, Chunk, Document, DocumentSource, DocumentStatus
from forest_bot.places import load_places
from forest_bot.repo import documents
from forest_bot.repo.documents import FileInfo
from forest_bot.repo.places import sync_places

pytestmark = pytest.mark.integration

GRAPH = load_places()


class FakeEmbedder:
    """Детермінований вектор із хеша тексту; запам'ятовує, що пішло в модель."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def aembed_passages(self, texts: Sequence[str]) -> npt.NDArray[np.float32]:
        self.seen.extend(texts)
        rows = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
            vector = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM)
            rows.append(vector / np.linalg.norm(vector))
        return np.array(rows, dtype=np.float32)


PARSED = ParsedDocument(
    blocks=(
        Block("Членські внески", "Членські внески", heading=True),
        Block("Шановні мешканці рукавички «Під дубом»!", "Членські внески"),
        Block("Заміна", "Заміна", heading=True),
        Block("Замість 4 жолудів можна віддати 2 горіхи.", "Заміна"),
    ),
    title="Членські внески",
)


async def new_document(
    session: AsyncSession, sha: str, lineage: Document | None = None
) -> Document:
    return await documents.create(
        session,
        place_id="pid-dubom",
        title="Членські внески",
        source=DocumentSource.ARCHIVE,
        file=FileInfo(file_name="x.txt", size_bytes=10, file_sha256=sha * 64),
        lineage_id=lineage.lineage_id if lineage else None,
    )


@pytest.fixture
async def places(session: AsyncSession) -> None:
    await sync_places(session, GRAPH)


@pytest.mark.usefixtures("places")
async def test_index_creates_chunks_and_activates(session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    document = await new_document(session, "a")
    assert await index_document(session, document, PARSED, embedder, GRAPH) is None

    chunks = (
        await session.scalars(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ord)
        )
    ).all()
    assert [(c.ord, c.section, c.text) for c in chunks] == [
        (0, "Членські внески", "Шановні мешканці рукавички «Під дубом»!"),
        (1, "Заміна", "Замість 4 жолудів можна віддати 2 горіхи."),
    ]
    assert (document.status, document.is_current) == (DocumentStatus.READY, True)
    # У модель іде фрагмент із контекстним заголовком, у БД — чистий текст
    assert embedder.seen[1].startswith("«Членські внески» — рукавички «Під дубом». Заміна.\n")
    # tsvector рахує БД: пошук за словом знаходить фрагмент
    found = await session.scalar(
        select(Chunk.ord).where(
            Chunk.document_id == document.id,
            Chunk.tsv.bool_op("@@")(func.plainto_tsquery("simple", "горіхи")),
        )
    )
    assert found == 1


@pytest.mark.usefixtures("places")
async def test_reindex_has_no_duplicates(session: AsyncSession) -> None:
    document = await new_document(session, "b")
    await index_document(session, document, PARSED, FakeEmbedder(), GRAPH)
    await index_document(session, document, PARSED, FakeEmbedder(), GRAPH)
    total = await session.scalar(select(func.count()).where(Chunk.document_id == document.id))
    assert total == 2


@pytest.mark.usefixtures("places")
async def test_new_version_replaces_old_only_when_indexed(session: AsyncSession) -> None:
    old = await new_document(session, "c")
    await index_document(session, old, PARSED, FakeEmbedder(), GRAPH)
    new = await new_document(session, "d", lineage=old)
    # Поки нова не проіндексована, діє стара
    assert (old.is_current, new.is_current) == (True, False)

    replaced = await index_document(session, new, PARSED, FakeEmbedder(), GRAPH)
    assert replaced is not None
    assert replaced.id == old.id
    await session.refresh(old)
    assert (old.is_current, new.is_current) == (False, True)


@pytest.mark.usefixtures("places")
async def test_nearest_chunk_by_vector(session: AsyncSession) -> None:
    embedder = FakeEmbedder()
    document = await new_document(session, "e")
    await index_document(session, document, PARSED, embedder, GRAPH)
    # Той самий текст із заголовком → той самий вектор → найближчий за косинусом (HNSW)
    query = (await embedder.aembed_passages([embedder.seen[1]]))[0].tolist()
    nearest = await session.scalar(
        select(Chunk.ord)
        .where(Chunk.document_id == document.id)
        .order_by(Chunk.embedding.cosine_distance(query))
        .limit(1)
    )
    assert nearest == 1


@pytest.mark.usefixtures("places")
async def test_document_without_text_is_not_indexed(session: AsyncSession) -> None:
    document = await new_document(session, "f")
    empty = ParsedDocument(blocks=(Block("Назва", "Назва", heading=True),), title="Назва")
    with pytest.raises(NothingToIndexError):
        await index_document(session, document, empty, FakeEmbedder(), GRAPH)
    assert document.is_current is False
