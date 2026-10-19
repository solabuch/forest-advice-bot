# syntax=docker/dockerfile:1

# Базові образи — за digest: збірка відтворювана, оновлює Dependabot
ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

# --- builder: віртуальне оточення лише з runtime-залежностями ---
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Шар залежностей перебудовується лише при зміні lock
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- модель embeddings: окремий шар, кешується, поки не змінився скрипт ---
FROM ${PYTHON_IMAGE} AS model
COPY scripts/fetch_model.py /tmp/fetch_model.py
RUN python /tmp/fetch_model.py /opt/models/multilingual-e5-small

# --- runtime ---
FROM ${PYTHON_IMAGE} AS runtime
# OCR сканів (ukr + eng) і старі .doc
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-ukr tesseract-ocr-eng antiword \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data \
    && chown app:app /app /data
COPY --from=model /opt/models /opt/models
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    EMBEDDING_MODEL_DIR=/opt/models/multilingual-e5-small \
    DRIVE_DIR=/data/drive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# Знімок архіву: бот знає його з першого старту, навіть без доступу до Drive (D-40)
COPY data/archive ./data/archive
# Eval і приклади в образі: якість можна перевірити без uv (README, «Якість»)
COPY --chown=app:app eval/qa.yaml ./eval/qa.yaml
COPY scripts/eval_qa.py ./scripts/eval_qa.py
COPY examples ./examples
USER app

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-m", "forest_bot", "health"]
ENTRYPOINT ["python", "-m", "forest_bot"]
CMD ["run"]
