"""Локальні embeddings: multilingual-e5-small, ONNX int8, CPU (ADR-03).

e5 навчали з префіксами: питання — «query: », фрагменти документів — «passage: ».
Без них схожість помітно гірша. Вектор — середнє по токенах із L2-нормалізацією,
тож косинусна схожість дорівнює скалярному добутку.
"""

import asyncio
import functools
import logging
import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import onnxruntime as ort
from tokenizers import Tokenizer

from forest_bot.models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

MODEL_NAME = "multilingual-e5-small"
# Довше модель не бачить: чанкер ріже фрагменти під цю межу (count_tokens)
MAX_TOKENS = 512
QUERY_PREFIX, PASSAGE_PREFIX = "query: ", "passage: "
# Потоки ONNX на один запит: решта ядер — боту й інжесту
THREADS = max(1, min(4, os.cpu_count() or 1))

Vectors = npt.NDArray[np.float32]


class EmbeddingError(Exception):
    """Модель не завантажилась; повідомлення — для людини."""


class Embedder:
    def __init__(self, model_dir: Path, *, threads: int = THREADS) -> None:
        model, tokenizer = model_dir / "model.onnx", model_dir / "tokenizer.json"
        missing = [path.name for path in (model, tokenizer) if not path.is_file()]
        if missing:
            raise EmbeddingError(
                f"немає {', '.join(missing)} у {model_dir} — завантаж модель: uv run poe model"
            )
        self._tokenizer = Tokenizer.from_file(str(tokenizer))
        # Обрізаємо самі: так count_tokens бачить справжню довжину
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(model), options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}

    def count_tokens(self, text: str) -> int:
        """Скільки токенів займе фрагмент разом із префіксом і службовими токенами."""
        return len(self._tokenizer.encode(PASSAGE_PREFIX + text).ids)

    def embed_query(self, text: str) -> Vectors:
        vector: Vectors = self._embed([QUERY_PREFIX + text])[0]
        return vector

    def embed_passages(self, texts: Sequence[str]) -> Vectors:
        return self._embed([PASSAGE_PREFIX + text for text in texts])

    async def aembed_query(self, text: str) -> Vectors:
        # CPU-робота — в окремому потоці, щоб бот не «застигав» під час інжесту
        return await asyncio.to_thread(self.embed_query, text)

    async def aembed_passages(self, texts: Sequence[str]) -> Vectors:
        return await asyncio.to_thread(self.embed_passages, texts)

    def _embed(self, texts: list[str]) -> Vectors:
        # По одному, без батчів: на CPU батчі не швидші (37 проти 42 мс на фрагмент),
        # а в int8 з динамічним квантуванням вектор залежав би від сусідів по батчу
        result = np.empty((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for row, encoding in enumerate(self._tokenizer.encode_batch(texts)):
            result[row] = self._run(self._truncate(encoding.ids))
        return result

    def _truncate(self, ids: list[int]) -> list[int]:
        # Зберігаємо службовий </s> у кінці, як це робить сам токенізатор
        return ids if len(ids) <= MAX_TOKENS else [*ids[: MAX_TOKENS - 1], ids[-1]]

    def _run(self, ids: list[int]) -> Vectors:
        input_ids = np.array([ids], dtype=np.int64)
        feed = {"input_ids": input_ids, "attention_mask": np.ones_like(input_ids)}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        hidden: Vectors = self._session.run(["last_hidden_state"], feed)[0][0]
        pooled = hidden.mean(axis=0)
        return (pooled / max(float(np.linalg.norm(pooled)), 1e-12)).astype(np.float32)


@functools.cache
def load_embedder(model_dir: Path) -> Embedder:
    """Одна модель на процес: самоперевірка, бот і інжест ділять той самий екземпляр."""
    embedder = Embedder(model_dir)
    logger.debug("Embeddings: %s з %s", MODEL_NAME, model_dir)
    return embedder
