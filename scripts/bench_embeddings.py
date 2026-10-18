"""Міні-бенчмарк embeddings: int8 проти fp32 на українських парах «питання → фрагмент».

Точність на справжніх питаннях з архіву (hit@k) — у eval (S-08); тут — чи не
зіпсувало квантування якість і скільки коштує один вектор на цьому CPU.

    uv run poe model
    uv run python scripts/fetch_model.py --variant fp32 .data/models/multilingual-e5-small-fp32
    uv run python scripts/bench_embeddings.py
"""

import functools
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from forest_bot.embeddings import Embedder

logger = logging.getLogger("bench_embeddings")

MODELS = Path(".data/models")
VARIANTS = {"int8": "multilingual-e5-small", "fp32": "multilingual-e5-small-fp32"}

# (питання, фрагмент з відповіддю); фрагменти інших пар — відволікачі
UKRAINIAN = [
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
    (
        "Скільки горіхів можна взяти в ліщині?",
        "У ліщині кожен звір бере до десяти горіхів на день; поросята до року — до п'яти.",
    ),
    (
        "Чи можна розпалювати багаття на узліссі?",
        "На узліссі вогонь заборонено з квітня до жовтня; взимку — лише на кам'яному колі.",
    ),
    (
        "Як поводитися біля мурашника?",
        "Біля мурашника не ставай на стежки мурах і не торкайся купола; обходь праворуч.",
    ),
    (
        "Що робити, якщо під вербою розлилася вода?",
        "Коли під вербою вода виходить з берегів, нори переносять вище, на пагорб за стежкою.",
    ),
    (
        "О котрій зачиняють комору під ялинкою?",
        "Комора під ялинкою відчинена з восьмої ранку до шостої вечора, у неділю — до обіду.",
    ),
    (
        "Чи платять за переправу через річку?",
        "Переправа на плоту коштує два жолуді; малеча до року переправляється безкоштовно.",
    ),
    (
        "Коли починається сезон грибів у соснах?",
        "Маслюки в соснах з'являються після перших серпневих дощів і ростуть до заморозків.",
    ),
    (
        "Кого кликати, якщо застряг у болоті?",
        "Застряг у болоті — гукай чаплю Сіру: вона чергує біля очерету щодня до заходу сонця.",
    ),
]
# Українське питання → фрагмент іншою мовою (D-30)
CROSS_LINGUAL = [
    (
        "Чи можна брати гриби біля мурашника після дощу?",
        "Mushrooms near the anthill should not be picked after rain.",
    ),
    (
        "Коли бобри лагодять греблю?",
        "Bobry naprawiają tamę w listopadzie, przed pierwszym lodem.",
    ),
    (
        "Скільки меду можна брати з дупла?",
        "Aus der Baumhöhle darf jedes Tier höchstens einen Löffel Honig pro Woche nehmen.",
    ),
]


def ranks(embedder: Embedder, pairs: list[tuple[str, str]], pool: list[str]) -> list[int]:
    """Місце правильного фрагмента серед усього пулу (1 — найкращий)."""
    passages = embedder.embed_passages(pool)
    result = []
    for question, passage in pairs:
        scores = passages @ embedder.embed_query(question)
        correct = scores[pool.index(passage)]
        result.append(int((scores > correct).sum()) + 1)
    return result


def timed_ms(action: Callable[[], object], repeat: int) -> float:
    action()
    started = time.perf_counter()
    for _ in range(repeat):
        action()
    return (time.perf_counter() - started) * 1000 / repeat


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    pool = [p for _, p in UKRAINIAN + CROSS_LINGUAL]
    vectors = {}
    logger.info(
        "| Варіант | Розмір | hit@1 укр | MRR укр | hit@1 між мовами | запит, мс | фрагмент, мс |"
    )
    logger.info("|---|---|---|---|---|---|---|")
    for name, folder in VARIANTS.items():
        model_dir = MODELS / folder
        if not (model_dir / "model.onnx").is_file():
            logger.info("| %s | немає в %s |", name, model_dir)
            continue
        embedder = Embedder(model_dir)
        uk = ranks(embedder, UKRAINIAN, pool)
        cross = ranks(embedder, CROSS_LINGUAL, pool)
        query_ms = timed_ms(functools.partial(embedder.embed_query, UKRAINIAN[0][0]), 20)
        passage_ms = timed_ms(functools.partial(embedder.embed_passages, pool), 3) / len(pool)
        vectors[name] = embedder.embed_passages(pool)
        logger.info(
            "| %s | %.0f МБ | %d/%d | %.3f | %d/%d | %.1f | %.1f |",
            name,
            (model_dir / "model.onnx").stat().st_size / 1e6,
            uk.count(1),
            len(uk),
            float(np.mean([1 / r for r in uk])),
            cross.count(1),
            len(cross),
            query_ms,
            passage_ms,
        )
    if len(vectors) == len(VARIANTS):
        cosine = (vectors["int8"] * vectors["fp32"]).sum(axis=1)
        logger.info(
            "\nКосинус int8 ↔ fp32 по фрагментах: середній %.4f, мінімальний %.4f",
            cosine.mean(),
            cosine.min(),
        )


if __name__ == "__main__":
    main()
