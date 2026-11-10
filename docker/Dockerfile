# Build the React SPA, fetch the pinned MiniLM model, then run FastAPI +
# FastMCP + static GUI in one image. Base images are pinned by digest and
# every dependency set is installed from a committed, hash-locked file.

FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS gui-build
WORKDIR /gui
COPY application/gui/package.json application/gui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY application/gui/ ./
RUN npm run build

# Hardcoded MiniLM ONNX (~87MB). No runtime model switch.
# The revision and both SHA-256 values are pinned. scripts/fetch_minilm_onnx.sh
# reads these three ARG lines, so keep the names and the NAME=value form.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS model
ARG MINILM_REVISION=1110a243fdf4706b3f48f1d95db1a4f5529b4d41
ARG MINILM_MODEL_SHA256=6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452
# A public file checksum, not a credential; "TOKEN" in the name trips DL3064.
# hadolint ignore=DL3064
ARG MINILM_TOKENIZER_SHA256=be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037
WORKDIR /models/all-MiniLM-L6-v2
# curl is needed in this stage only; the stage is not shipped.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && base="https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/${MINILM_REVISION}" \
    && curl -fsSL --retry 3 "$base/onnx/model.onnx" -o model.onnx \
    && curl -fsSL --retry 3 "$base/tokenizer.json" -o tokenizer.json \
    && printf '%s  model.onnx\n%s  tokenizer.json\n' \
         "$MINILM_MODEL_SHA256" "$MINILM_TOKENIZER_SHA256" > SHA256SUMS \
    && sha256sum -c SHA256SUMS \
    && rm SHA256SUMS

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

ARG INSTALL_DEV=0

# Pending Debian security updates for the pinned base, then the fixed runtime user.
RUN apt-get update && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 memchentic \
    && useradd --uid 10001 --gid 10001 --home-dir /opt/memchentic --shell /usr/sbin/nologin memchentic

WORKDIR /opt/memchentic

# Dependency layer: rebuilt only when a lock file changes. pip refuses any
# file whose SHA-256 is not in the lock.
COPY application/api/requirements.lock application/api/requirements-dev.lock ./
RUN python -m venv /opt/venv \
    && pip install --no-cache-dir --require-hashes -r requirements.lock \
    && if [ "$INSTALL_DEV" = "1" ]; then \
         pip install --no-cache-dir --require-hashes -r requirements-dev.lock; \
       fi

# Model layer: rebuilt only when the revision or a checksum changes.
COPY --from=model /models ./models

# Source layers. Everything stays root-owned: uid 10001 reads, never writes.
COPY application/api/pyproject.toml application/api/alembic.ini README.md LICENSE ./
COPY application/api/alembic ./alembic
COPY application/api/src ./src
COPY application/mcp/src/memchentic/mcp ./src/memchentic/mcp
COPY --from=gui-build /gui/dist ./gui/dist

# Install the project itself, editable, so memchentic.main resolves
# /opt/memchentic as API_ROOT. The build uses the locked setuptools already in
# the virtual environment and downloads nothing. pip check fails the build
# when pyproject.toml names a dependency that the lock files do not provide.
RUN pip install --no-cache-dir --no-deps --no-build-isolation -e . \
    && pip check

# Static labels for local builds; the publish script adds source, revision and version.
LABEL org.opencontainers.image.title="memchentic" \
      org.opencontainers.image.description="Self-hosted multi-user agent memory over MCP and a React UI" \
      org.opencontainers.image.licenses="MIT"

USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"]

CMD ["uvicorn", "memchentic.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "5"]
