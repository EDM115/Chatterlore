FROM python:3.12-slim AS builder

ARG UV_VERSION=0.12.0

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN pip install --no-cache-dir "uv==$UV_VERSION"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system pzp && adduser --system --ingroup pzp --home /app pzp && mkdir -p /app/data && chown -R pzp:pzp /app

COPY --from=builder --chown=pzp:pzp /opt/venv /opt/venv
COPY --chown=pzp:pzp alembic.ini ./alembic.ini
COPY --chown=pzp:pzp alembic ./alembic
COPY --chown=pzp:pzp docs ./docs

USER pzp

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 CMD pzp-bot doctor || exit 1

CMD ["pzp-bot", "polling"]
