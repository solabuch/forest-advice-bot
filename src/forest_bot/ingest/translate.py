"""Документ іншою мовою (D-30): для пошуку — український переклад фрагментів.

Багатомовна модель embeddings зводить «Коли збирати лисички біля мурашника?» з англійським
«Pick them two days after rain» лише до косинуса ≈ 0,78 — нижче порогу пошуку, а з
перекладом — ≈ 0,86. Тож вектор фрагмента рахуємо з перекладу, а у фрагмент після
оригіналу дописуємо переклад: без нього пошук за словами документа не бачив, і він
програвав власним документам рукавички. Модель бачить обидва тексти — числа й назви
звіряє з оригіналом (D-48). Переклад не вдався — індексуємо лише оригінал: документ не
губиться, лише гірше шукається.
"""

import logging
from collections.abc import Sequence
from typing import Protocol

from forest_bot.llm.client import GroqClient, LlmError
from forest_bot.llm.prompts import TRANSLATE_PROMPT, Translation, translate_messages

logger = logging.getLogger(__name__)

# Після оригіналу у фрагменті — його переклад для пошуку
TRANSLATION_MARK = "[Переклад українською для пошуку]"
# Менше кирилиці серед літер — документ іншою мовою
CYRILLIC_SHARE = 0.3
# Коротший текст — не варто LLM-виклику
MIN_LETTERS = 40
# Фрагментів за один виклик: ~300 токенів кожен, разом із перекладом — у межах TPM Groq
BATCH = 6
CHARS_PER_TOKEN = 2


class Translator(Protocol):
    async def to_ukrainian(self, texts: Sequence[str]) -> list[str] | None: ...


def is_foreign(texts: Sequence[str]) -> bool:
    letters = [ch for text in texts for ch in text if ch.isalpha()]
    if len(letters) < MIN_LETTERS:
        return False
    cyrillic = sum("Ѐ" <= ch <= "ӿ" for ch in letters)
    return cyrillic / len(letters) < CYRILLIC_SHARE


class LlmTranslator:
    def __init__(self, llm: GroqClient) -> None:
        self.llm = llm

    async def to_ukrainian(self, texts: Sequence[str]) -> list[str] | None:
        translated: list[str] = []
        for start in range(0, len(texts), BATCH):
            batch = list(texts[start : start + BATCH])
            # Український текст довший за англійський у токенах — із запасом
            max_tokens = sum(len(t) for t in batch) // CHARS_PER_TOKEN * 2 + 100
            try:
                result, completion = await self.llm.complete_json(
                    translate_messages(batch), Translation, max_tokens=max_tokens
                )
            except LlmError as error:
                logger.warning("Переклад для пошуку не вдався (%s) — індексую оригінал", error)
                return None
            if len(result.texts) != len(batch):
                logger.warning("Переклад для пошуку: інша кількість фрагментів — індексую оригінал")
                return None
            translated.extend(result.texts)
            logger.info(
                "Переклад для пошуку (%s): %d фрагментів, %s",
                TRANSLATE_PROMPT,
                len(batch),
                completion.model,
            )
        return translated
