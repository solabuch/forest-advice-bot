# 0009. Інструменти: uv, ruff, mypy, pytest, poe

Статус: прийнято · 17.10.2026 · Вимоги: C-01

## Контекст

- Розробка на Windows, CI на Linux, запуск у Docker.
- Перевірки локально й у CI мають бути однаковими.

## Рішення

- **uv** — lock-файл, версія Python і віртуальне оточення:
  - `required-version` фіксує версію uv, щоб формат `uv.lock` не залежав від машини.
- **Якість коду:**
  - ruff — лінтер і форматер;
  - mypy `--strict`;
  - pytest + pytest-asyncio + coverage.
- **Задачі** — poe (`lint`, `types`, `test`, `check`). Кросплатформні, бо на Windows немає `make`.
- **pre-commit:** ruff, `uv-lock`, gitleaks, базові хуки. Файли архіву хуки не змінюють — їхні байти лишаються як на Диску.
- **Версія пакета** — у `src/forest_bot/__init__.py` (dynamic для hatchling):
  - статична версія в `pyproject.toml` записується в `uv.lock`, і її підняття в release-PR ламало б `uv sync --locked`;
  - перевірено підняттям 0.1.0 → 0.2.0.

## Наслідки

- `uv run poe check` дає те саме, що й CI.
- Новий розробник ставить лише uv.

## Альтернативи

- **poetry** — повільніший, а Python окремо ставити все одно треба.
- **black + flake8 + isort** — три інструменти замість одного ruff.
- **make** — немає на Windows.
