"""Embeddings: multilingual-e5-small int8 — розмірність, нормалізація, українські пари.

Тести з моделлю локально пропускаються, якщо її не завантажено (uv run poe model);
у CI модель є завжди.
"""

import os
from pathlib import Path

import numpy as np
import pytest

from forest_bot.embeddings import MAX_TOKENS, Embedder, EmbeddingError, load_embedder
from forest_bot.models import EMBEDDING_DIM
from forest_bot.startup import check_embeddings
from tests.factories import make_settings

MODEL_DIR = Path(__file__).resolve().parents[2] / ".data" / "models" / "multilingual-e5-small"

# Питання звіра → фрагмент, де відповідь; решта фрагментів — сусіди-відволікачі
PAIRS = [
    (
        "Коли збирати жолуді?",
        "Жолуді під дубом збирають у жовтні, коли вони самі падають. Не більше трьох на звіра.",
    ),
    (
        "В які дні можна перейти греблю?",
        "Гребля бобрів відкривається у вівторок і п'ятницю. Переходити лише вдень.",
    ),
    (
        "Де білки зберігають шишки?",
        "У соснах шишки визрівають наприкінці зими; білки ховають їх у дуплах.",
    ),
    (
        "Чим годувати їжаченят восени?",
        "Їжаченятам восени дають яблука й дощових черв'яків, молока не дають.",
    ),
]


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    if not (MODEL_DIR / "model.onnx").is_file():
        if os.environ.get("CI"):
            pytest.fail("модель embeddings не завантажено в CI")
        pytest.skip("немає моделі embeddings — uv run poe model")
    return load_embedder(MODEL_DIR)


def test_missing_model_is_explained(tmp_path: Path) -> None:
    with pytest.raises(EmbeddingError, match="uv run poe model"):
        Embedder(tmp_path)


async def test_check_without_model_is_fatal(tmp_path: Path) -> None:
    result = await check_embeddings(make_settings(embedding_model_dir=tmp_path))
    assert (result.ok, result.fatal) == (False, True)
    assert "model.onnx" in result.detail


@pytest.mark.model
async def test_check_reports_model() -> None:
    if not MODEL_DIR.is_dir():
        pytest.skip("немає моделі embeddings")
    result = await check_embeddings(make_settings(embedding_model_dir=MODEL_DIR))
    assert result.ok
    assert result.detail.startswith(f"multilingual-e5-small, {EMBEDDING_DIM} вимірів")


@pytest.mark.model
def test_vectors_are_normalized(embedder: Embedder) -> None:
    vectors = embedder.embed_passages([passage for _, passage in PAIRS])
    assert vectors.shape == (len(PAIRS), EMBEDDING_DIM)
    assert vectors.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5)
    assert embedder.embed_passages([]).shape == (0, EMBEDDING_DIM)


@pytest.mark.model
def test_question_finds_its_passage(embedder: Embedder) -> None:
    passages = embedder.embed_passages([passage for _, passage in PAIRS])
    questions = np.stack([embedder.embed_query(question) for question, _ in PAIRS])
    best = (questions @ passages.T).argmax(axis=1)
    assert best.tolist() == list(range(len(PAIRS)))


@pytest.mark.model
def test_vector_does_not_depend_on_neighbours(embedder: Embedder) -> None:
    # Повторна індексація того самого фрагмента дає той самий вектор
    alone = embedder.embed_passages([PAIRS[0][1]])[0]
    together = embedder.embed_passages([PAIRS[1][1], PAIRS[0][1]])[1]
    np.testing.assert_array_equal(alone, together)


@pytest.mark.model
def test_long_text_is_truncated(embedder: Embedder) -> None:
    text = "Жолуді під дубом збирають у жовтні. " * 200
    assert embedder.count_tokens(text) > MAX_TOKENS
    assert embedder.embed_passages([text]).shape == (1, EMBEDDING_DIM)


@pytest.mark.model
async def test_async_matches_sync(embedder: Embedder) -> None:
    question = PAIRS[0][0]
    np.testing.assert_array_equal(
        await embedder.aembed_query(question), embedder.embed_query(question)
    )
    passages = [passage for _, passage in PAIRS[:2]]
    np.testing.assert_array_equal(
        await embedder.aembed_passages(passages), embedder.embed_passages(passages)
    )
