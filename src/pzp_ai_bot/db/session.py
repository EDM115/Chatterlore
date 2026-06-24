from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import Base


def ensure_parent_for_sqlite(settings: Settings) -> None:
    sqlite_path = settings.sqlite_path
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)


def create_engine(settings: Settings) -> AsyncEngine:
    ensure_parent_for_sqlite(settings)
    return create_async_engine(settings.db_url, future=True)


def create_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_engine(settings)
    return async_sessionmaker(engine, expire_on_commit=False)


def alembic_project_root() -> Path:
    source_root = Path(__file__).resolve().parents[3]
    for candidate in (Path.cwd(), source_root):
        if (candidate / "alembic.ini").is_file() and (candidate / "alembic").is_dir():
            return candidate
    return source_root


def create_alembic_config(settings: Settings) -> Config:
    project_root = alembic_project_root()
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.attributes["settings"] = settings
    return config


def upgrade_schema(settings: Settings, revision: str = "head") -> None:
    settings.memory_dir.mkdir(parents=True, exist_ok=True)
    ensure_parent_for_sqlite(settings)
    command.upgrade(create_alembic_config(settings), revision)


async def create_schema(settings: Settings) -> None:
    settings.memory_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def resolve_output_path(path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    return out
