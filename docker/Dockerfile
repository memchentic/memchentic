# Build the React SPA, then run FastAPI + FastMCP + static GUI in one image.
FROM node:22-alpine AS gui-build
WORKDIR /gui
COPY gui/package.json gui/package-lock.json* ./
RUN npm install
COPY gui/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    EMBEDDING_MODEL_PATH=/opt/memchentic/models/all-MiniLM-L6-v2

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/memchentic

COPY api/pyproject.toml ./
COPY api/src ./src
COPY api/alembic.ini ./
COPY api/alembic ./alembic
COPY README.md LICENSE ./
COPY --from=gui-build /gui/dist ./gui/dist

RUN python -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[dev]"

# Bake default MiniLM ONNX into the image (~87MB). Override at runtime with
# EMBEDDING_MODEL_PATH if pointing at another local model directory.
ARG MINILM_REPO=https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main
RUN mkdir -p /opt/memchentic/models/all-MiniLM-L6-v2 \
    && curl -fsSL "$MINILM_REPO/onnx/model.onnx" \
        -o /opt/memchentic/models/all-MiniLM-L6-v2/model.onnx \
    && curl -fsSL "$MINILM_REPO/tokenizer.json" \
        -o /opt/memchentic/models/all-MiniLM-L6-v2/tokenizer.json

EXPOSE 8080

CMD ["uvicorn", "memchentic.main:app", "--host", "0.0.0.0", "--port", "8080"]
