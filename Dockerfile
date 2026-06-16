FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system .

COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY docs ./docs

CMD ["pzp-bot", "polling"]
