FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/memchentic

COPY api/pyproject.toml ./
COPY api/src ./src
COPY api/alembic.ini ./
COPY api/alembic ./alembic
COPY README.md LICENSE ./

RUN python -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[dev]"

RUN mkdir -p /opt/memchentic/models /opt/memchentic/gui/dist

EXPOSE 8080

CMD ["uvicorn", "memchentic.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload", "--reload-dir", "/opt/memchentic/src"]
