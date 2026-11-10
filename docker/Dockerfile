# Build the React SPA, then run FastAPI + FastMCP + static GUI in one image.
FROM node:22-alpine AS gui-build
WORKDIR /gui
COPY application/gui/package.json application/gui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY application/gui/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

ARG INSTALL_DEV=0

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 memchentic \
    && useradd --uid 10001 --gid 10001 --home-dir /opt/memchentic --shell /usr/sbin/nologin memchentic

WORKDIR /opt/memchentic

COPY application/api/pyproject.toml ./
COPY application/api/uv.lock* ./
COPY application/api/src ./src
COPY application/mcp/src/memchentic/mcp ./src/memchentic/mcp
COPY application/api/alembic.ini ./
COPY application/api/alembic ./alembic
COPY README.md LICENSE ./
COPY --from=gui-build /gui/dist ./gui/dist

RUN python -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && if [ "$INSTALL_DEV" = "1" ]; then \
         pip install --no-cache-dir -e ".[dev]"; \
       else \
         pip install --no-cache-dir -e .; \
       fi

# Bake hardcoded MiniLM ONNX under the API tree (~87MB). No runtime model switch.
ARG MINILM_REPO=https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main
RUN mkdir -p /opt/memchentic/models/all-MiniLM-L6-v2 \
    && curl -fsSL "$MINILM_REPO/onnx/model.onnx" \
        -o /opt/memchentic/models/all-MiniLM-L6-v2/model.onnx \
    && curl -fsSL "$MINILM_REPO/tokenizer.json" \
        -o /opt/memchentic/models/all-MiniLM-L6-v2/tokenizer.json \
    && chown -R memchentic:memchentic /opt/memchentic /opt/venv

USER 10001:10001

EXPOSE 8080

CMD ["uvicorn", "memchentic.main:app", "--host", "0.0.0.0", "--port", "8080"]
