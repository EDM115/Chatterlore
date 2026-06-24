from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from pzp_ai_bot.app_context import build_context
from pzp_ai_bot.config import Settings, get_settings
from pzp_ai_bot.db.session import upgrade_schema
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.service import (
    audit_events_jsonl,
    format_audit_events,
    format_memory_status,
    format_runtime_settings,
    parse_memory_export_format,
    parse_runtime_settings_args,
)
from pzp_ai_bot.telegram.polling import run_polling
from pzp_ai_bot.telegram.webhook import run_webhook

app = typer.Typer(help="Telegram scoped memory assistant.")
console = Console()


def run(coro):
    return asyncio.run(coro)


@app.command()
def migrate() -> None:
    """Create or upgrade the local database schema."""
    settings = get_settings()
    upgrade_schema(settings)
    console.print(f"Schema ready at [bold]{settings.db_url}[/bold]")


@app.command()
def polling() -> None:
    """Delete any webhook and run Telegram long polling."""
    settings = get_settings()
    context = run(build_context(settings))
    run(run_polling(context))


@app.command()
def webhook() -> None:
    """Set Telegram webhook and serve the FastAPI webhook app."""
    settings = get_settings()
    context = run(build_context(settings))
    run(run_webhook(context))


@app.command("summarize-now")
def summarize_now(
    scope: Annotated[
        str, typer.Option(help="Scope: all, <chat_id>, <chat_id>:<thread_id>.")
    ] = "all",
) -> None:
    """Summarize pending messages for a scope and compact MEMORY.md."""
    settings = get_settings()
    context = run(build_context(settings))
    try:
        parsed = parse_scope(scope)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    count = run(context.memory.summarize_now(parsed))
    run(context.memory.compact_memory())
    target = "all concrete scopes" if parsed.is_global else parsed.key
    console.print(f"Summarized {count} pending messages for {target}.")


@app.command("export-memory")
def export_memory(
    scope: Annotated[
        str, typer.Option(help="Scope: all, <chat_id>, <chat_id>:<thread_id>.")
    ] = "all",
    memory_format: Annotated[
        str, typer.Option("--format", help="Output format: md or jsonl.")
    ] = "md",
    out: Annotated[Path | None, typer.Option(help="Optional output file path.")] = None,
) -> None:
    """Export Markdown or JSONL memory."""
    try:
        fmt = parse_memory_export_format(memory_format)
        parsed = parse_scope(scope)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    settings = get_settings()
    context = run(build_context(settings))
    payload = run(context.memory.export_memory(None if parsed.is_global else parsed, fmt=fmt))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        console.print(f"Wrote {out}")
        return
    console.print(payload)


@app.command("import-jsonl")
def import_jsonl(
    file: Annotated[Path, typer.Option("--file", exists=True, file_okay=True, dir_okay=False)],
    chat_id: Annotated[int, typer.Option("--chat-id")],
    thread_id: Annotated[int | None, typer.Option("--thread-id")] = None,
) -> None:
    """Import JSONL knowledge items into a chat/topic scope."""
    settings = get_settings()
    context = run(build_context(settings))
    scope = Scope(chat_id, thread_id)
    try:
        count = run(context.memory.import_jsonl(file, scope))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Imported {count} memory items into {scope.key}.")


@app.command("import-telegram-json")
def import_telegram_json(
    file: Annotated[Path, typer.Option("--file", exists=True, file_okay=True, dir_okay=False)],
    chat_id: Annotated[int, typer.Option("--chat-id")],
    thread_id: Annotated[int | None, typer.Option("--thread-id")] = None,
) -> None:
    """Import Telegram Desktop JSON or Bot API updates into learned raw-message memory."""
    settings = get_settings()
    context = run(build_context(settings))
    scope = Scope(chat_id, thread_id)
    try:
        result = run(context.memory.import_telegram_desktop_json(file, scope))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"Imported {result.imported} Telegram JSON messages into {scope.key}; "
        f"skipped {result.skipped}."
    )


@app.command("memory-status")
def memory_status(
    scope: Annotated[
        str, typer.Option(help="Scope: all, <chat_id>, <chat_id>:<thread_id>.")
    ] = "all",
) -> None:
    """Show scoped message, memory and AI usage status."""
    try:
        parsed = parse_scope(scope)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    settings = get_settings()
    context = run(build_context(settings))
    status = run(context.memory.memory_status(None if parsed.is_global else parsed))
    console.print(format_memory_status(status))


@app.command("doctor")
def doctor() -> None:
    """Check local provider, database and memory-path health without starting Telegram."""
    settings = get_settings()
    checks = provider_health_checks(settings)
    for name, ok, detail in checks:
        status = "ok" if ok else "error"
        console.print(f"{name}: {status} - {detail}")
    if any(not ok for _name, ok, _detail in checks):
        raise typer.Exit(code=1)


@app.command("audit-events")
def audit_events(
    limit: Annotated[int, typer.Option(help="Maximum events to return.")] = 50,
    event_type: Annotated[str | None, typer.Option(help="Optional event type filter.")] = None,
    scope: Annotated[
        str, typer.Option(help="Scope: all, <chat_id>, <chat_id>:<thread_id>.")
    ] = "all",
    output_format: Annotated[
        str, typer.Option("--format", help="Output format: text or jsonl.")
    ] = "text",
    out: Annotated[Path | None, typer.Option(help="Optional output file path.")] = None,
) -> None:
    """Export recent audit events from bot_events."""
    if output_format not in {"text", "jsonl"}:
        raise typer.BadParameter("format must be text or jsonl")
    try:
        parsed = parse_scope(scope)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    settings = get_settings()
    context = run(build_context(settings))
    events = run(
        context.memory.audit_events(
            limit=limit, event_type=event_type, scope=None if parsed.is_global else parsed
        )
    )
    payload = (
        audit_events_jsonl(events) if output_format == "jsonl" else format_audit_events(events)
    )
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        console.print(f"Wrote {out}")
        return
    console.print(payload)


@app.command("runtime-settings")
def runtime_settings(
    set_values: Annotated[
        list[str] | None,
        typer.Option("--set", help="Set key=value for a safe non-secret runtime setting."),
    ] = None,
    reload_settings: Annotated[
        bool, typer.Option("--reload", help="Reload runtime settings from the database.")
    ] = False,
) -> None:
    """View, edit, or reload safe database-backed runtime settings."""
    settings = get_settings()
    context = run(build_context(settings))
    if reload_settings:
        run(context.memory.refresh_runtime_settings())
    updates: dict[str, str] = {}
    for item in set_values or []:
        if "=" not in item:
            raise typer.BadParameter("runtime settings must use key=value form")
        key, value = item.split("=", 1)
        updates[key.strip()] = value.strip()
    if updates:
        parsed_updates = parse_runtime_settings_args(updates)
        unknown = sorted(
            set(updates)
            - set(parsed_updates)
            - {"top_k", "provider", "system_prompt", "raw_retention_days"}
        )
        if unknown:
            raise typer.BadParameter(f"unknown runtime setting: {unknown[0]}")
        run(context.memory.set_runtime_settings(parsed_updates))
    console.print(format_runtime_settings(run(context.memory.list_runtime_settings())))


@app.command("db-stats")
def db_stats() -> None:
    """Show SQLite file and table statistics."""
    settings = get_settings()
    path = require_sqlite_path(settings)
    upgrade_schema(settings)
    stats = sqlite_stats(path)
    for key, value in stats.items():
        console.print(f"{key}: {value}")


@app.command("db-integrity")
def db_integrity() -> None:
    """Run PRAGMA integrity_check on the SQLite database."""
    settings = get_settings()
    path = require_sqlite_path(settings)
    upgrade_schema(settings)
    with sqlite3.connect(path) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    value = str(result[0] if result else "unknown")
    console.print(f"integrity_check: {value}")
    if value.lower() != "ok":
        raise typer.Exit(code=1)


@app.command("db-vacuum")
def db_vacuum() -> None:
    """Run SQLite VACUUM."""
    settings = get_settings()
    path = require_sqlite_path(settings)
    upgrade_schema(settings)
    with sqlite3.connect(path) as connection:
        connection.execute("VACUUM")
    console.print(f"Vacuumed {path}")


@app.command("db-backup")
def db_backup(out: Annotated[Path, typer.Option("--out", help="Backup SQLite file path.")]) -> None:
    """Back up the SQLite database file."""
    settings = get_settings()
    path = require_sqlite_path(settings)
    upgrade_schema(settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as source, sqlite3.connect(out) as destination:
        source.backup(destination)
    console.print(f"Backed up {path} to {out}")


@app.command("show-config")
def show_config() -> None:
    """Show non-secret runtime configuration."""
    settings = get_settings()
    visible = {
        "runtime_mode": settings.runtime_mode,
        "db_url": settings.db_url,
        "memory_dir": str(settings.memory_dir),
        "ai_provider": settings.ai_provider,
        "chat_model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        "provider_profiles": ",".join(sorted(settings.provider_profiles)),
        "system_prompt_template": settings.system_prompt_template,
        "reasoning_level": settings.reasoning_level,
        "learning_default_enabled": settings.learning_default_enabled,
        "runtime_settings_refresh_seconds": settings.runtime_settings_refresh_seconds,
        "admin_cache_ttl_seconds": settings.admin_cache_ttl_seconds,
        "forget_confirm_ttl_seconds": settings.forget_confirm_ttl_seconds,
        "log_level": settings.log_level,
        "bot_admins_count": len(settings.bot_admin_ids),
    }
    for key, value in visible.items():
        console.print(f"{key}: {value}")


def require_sqlite_path(settings: Settings) -> Path:
    path = settings.sqlite_path
    if path is None:
        raise typer.BadParameter("DB maintenance commands require sqlite+aiosqlite DB_URL")
    return path


def quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sqlite_stats(path: Path) -> dict[str, int | str]:
    stats: dict[str, int | str] = {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%' order by name"
        ).fetchall()
        for (table_name,) in rows:
            quoted_table_name = quote_sqlite_identifier(str(table_name))
            count = connection.execute(
                f"select count(*) from {quoted_table_name}"  # noqa: S608
            ).fetchone()[0]
            stats[f"table_{table_name}"] = int(count)
    return stats


def provider_health_checks(settings: Settings) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    try:
        profiles = settings.provider_profiles
    except ValueError as exc:
        return [("provider_profiles", False, str(exc))]
    for name, profile in sorted(profiles.items()):
        if profile.provider == "openai_compatible" and not profile.openai_api_key:
            checks.append((f"provider:{name}", False, "missing OpenAI-compatible API key"))
        elif (
            profile.provider == "local"
            and importlib.util.find_spec("sentence_transformers") is None
        ):
            checks.append(
                (
                    f"provider:{name}",
                    False,
                    "sentence-transformers is not installed; run `uv sync --extra local`",
                )
            )
        else:
            checks.append(
                (
                    f"provider:{name}",
                    True,
                    f"{profile.provider} chat={profile.chat_model} "
                    f"embedding={profile.embedding_model}",
                )
            )
    sqlite_path = settings.sqlite_path
    if sqlite_path is None:
        checks.append(("database", True, settings.db_url))
    else:
        parent = sqlite_path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            probe = parent / ".pzp_ai_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            checks.append(("database", True, str(sqlite_path)))
        except OSError as exc:
            checks.append(("database", False, f"cannot write SQLite parent {parent}: {exc}"))
    try:
        settings.memory_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.memory_dir / ".pzp_ai_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks.append(("memory_dir", True, str(settings.memory_dir)))
    except OSError as exc:
        checks.append(
            ("memory_dir", False, f"cannot write memory dir {settings.memory_dir}: {exc}")
        )
    return checks


if __name__ == "__main__":
    app()
