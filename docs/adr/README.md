# Архітектурні рішення (ADR)

Формат: Статус · Контекст · Рішення · Наслідки · Альтернативи. Ідентифікатори вимог (C-, R-, N-, D-) — з [requirements.md](../requirements.md).

| # | Рішення | Статус |
|---|---|---|
| [0001](0001-telegram-framework-aiogram.md) | Telegram-фреймворк — aiogram 3 | прийнято |
| [0002](0002-storage-postgres-pgvector.md) | Одне сховище — Postgres + pgvector | прийнято |
| [0003](0003-local-multilingual-embeddings.md) | Локальна багатомовна модель embeddings | прийнято |
| [0004](0004-llm-groq-fallback.md) | LLM — Groq з fallback між моделями | прийнято |
| [0005](0005-hybrid-search.md) | Гібридний пошук з фільтром області в SQL | прийнято |
| [0006](0006-background-jobs-in-postgres.md) | Фонові задачі — черга в Postgres | прийнято |
| [0007](0007-long-polling.md) | Long polling замість webhook | прийнято |
| [0008](0008-github-flow.md) | GitHub Flow, squash, Conventional Commits | прийнято |
| [0009](0009-tooling.md) | Інструменти: uv, ruff, mypy, pytest, poe | прийнято |
| [0010](0010-e2e-in-process.md) | E2E-тести всередині процесу | прийнято |
| [0011](0011-prebuilt-multiarch-image.md) | Готовий multi-arch образ у GHCR | прийнято |
| [0012](0012-delivery-without-server.md) | Здача без сервера: автоматизація на GitHub | прийнято |
| [0013](0013-private-until-submission.md) | Репо приватне до здачі | прийнято |
| [0014](0014-archive-sync-from-drive.md) | «Лісовий архів»: синхронізація з Drive + знімок у репо | прийнято |
| [0015](0015-ocr-tesseract.md) | OCR — Tesseract | прийнято |
| [0016](0016-document-place-detection.md) | Місце документа: фраза → LLM → перепитати | прийнято |
| [0017](0017-access-by-invitation.md) | Доступ за запрошенням | прийнято |
| [0018](0018-place-in-question.md) | Місце в питанні: словник назв → LLM → кнопки | прийнято |
