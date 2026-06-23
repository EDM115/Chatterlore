from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from pzp_ai_bot.config import Settings, get_settings
from pzp_ai_bot.db.models import Base
from pzp_ai_bot.db.session import ensure_parent_for_sqlite

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def configured_settings() -> Settings:
    settings = config.attributes.get("settings")
    if isinstance(settings, Settings):
        return settings
    return get_settings()


def run_migrations_offline() -> None:
    settings = configured_settings()
    url = settings.db_url.replace("+aiosqlite", "")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = configured_settings()
    ensure_parent_for_sqlite(settings)
    config.set_main_option("sqlalchemy.url", settings.db_url.replace("+aiosqlite", ""))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
