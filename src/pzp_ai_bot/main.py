from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import typer
from rich.console import Console

from pzp_ai_bot.ai.factory import ProviderRegistry
from pzp_ai_bot.app_context import AppContext, build_context
from pzp_ai_bot.config import Settings, get_settings
from pzp_ai_bot.control_plane.development import run_control_plane_development
from pzp_ai_bot.control_plane.secrets.service import ProviderSecretService
from pzp_ai_bot.db.session import create_session_factory, upgrade_schema
from pzp_ai_bot.embedding_cli import embedding_index_app, local_model_app
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.service import (
    MemoryExportFormat,
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
secrets_app = typer.Typer(help="Inspect and rotate encrypted provider credentials.")
app.add_typer(secrets_app, name="secrets")
app.add_typer(embedding_index_app, name="embedding-index")
app.add_typer(local_model_app, name="local-model")
console = Console()


def redact_url_credentials(url: str) -> str:
    parsed = urlsplit(url)
    if "@" not in parsed.netloc:
        return url
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (parsed.scheme, f"[REDACTED]@{host}", parsed.path, parsed.query, parsed.fragment)
    )


def run(coro):
    return asyncio.run(coro)


def build_secret_service(settings: Settings) -> ProviderSecretService:
    return ProviderSecretService(
        settings, create_session_factory(settings), ProviderRegistry(settings)
    )


@app.command()
def migrate() -> None:
    """Create or upgrade the local database schema."""
    settings = get_settings()
    upgrade_schema(settings)
    console.print(f"Schema ready at [bold]{redact_url_credentials(settings.db_url)}[/bold]")


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


@app.command("control-plane-dev")
def control_plane_dev() -> None:
    """Run the loopback development control plane, optionally connected to Telegram."""
    try:
        run(run_control_plane_development(get_settings()))
    except (ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc


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
    target_scope = None if parsed.is_global else parsed
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        run(write_memory_export(context, target_scope, fmt=fmt, out=out))
        console.print(f"Wrote {out}")
        return
    run(write_memory_export(context, target_scope, fmt=fmt, out=None))


async def write_memory_export(
    context: AppContext, scope: Scope | None, *, fmt: MemoryExportFormat, out: Path | None
) -> None:
    if out is not None:
        with out.open("w", encoding="utf-8") as handle:
            async for chunk in context.memory.export_memory_chunks(scope, fmt=fmt):
                handle.write(chunk.text)
        return
    async for chunk in context.memory.export_memory_chunks(scope, fmt=fmt):
        console.file.write(chunk.text)
    console.file.flush()


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
    _import_telegram_command(file, Scope(chat_id, thread_id), label="Telegram JSON", json_only=True)


@app.command("import-telegram")
def import_telegram(
    file: Annotated[Path, typer.Option("--file", exists=True, file_okay=True, dir_okay=True)],
    chat_id: Annotated[int, typer.Option("--chat-id")],
    thread_id: Annotated[int | None, typer.Option("--thread-id", min=1)] = None,
) -> None:
    """Import a JSON/HTML file or export directory (JSON preferred when both exist)."""
    _import_telegram_command(file, Scope(chat_id, thread_id), label="Telegram")


@app.command("import-telegram-html")
def import_telegram_html(
    file: Annotated[Path, typer.Option("--file", exists=True, file_okay=True, dir_okay=False)],
    chat_id: Annotated[int, typer.Option("--chat-id")],
    thread_id: Annotated[int | None, typer.Option("--thread-id", min=1)] = None,
) -> None:
    """Import Telegram Desktop HTML without executing scripts or fetching linked resources."""
    if file.suffix.lower() not in {".html", ".htm"}:
        raise typer.BadParameter("import-telegram-html requires an HTML export file")
    _import_telegram_command(file, Scope(chat_id, thread_id), label="Telegram HTML")


def _import_telegram_command(
    file: Path, scope: Scope, *, label: str, json_only: bool = False
) -> None:
    async def execute():
        context = await build_context(get_settings())
        try:
            if json_only:
                return await context.memory.import_telegram_desktop_json(file, scope)
            return await context.memory.import_telegram_export(file, scope)
        finally:
            try:
                await context.memory.provider_registry.aclose()
            finally:
                await context.session_factory.kw["bind"].dispose()

    try:
        result = run(execute())
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if scope.thread_id is None:
        console.print(
            "Target: aggregate chat memory only, topic ownership is not inferred. Use --thread-id for an individual topic export."  # noqa: E501
        )
    console.print(
        f"Imported {result.imported} {label} messages into {scope.key}, skipped {result.skipped}."
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
            - {"candidate_limit", "top_k", "provider", "system_prompt", "raw_retention_days"}
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
        "db_url": redact_url_credentials(settings.db_url),
        "memory_dir": str(settings.memory_dir),
        "ai_provider": settings.ai_provider,
        "chat_model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        "provider_profiles": ",".join(sorted(settings.provider_profiles)),
        "system_prompt_template": settings.system_prompt_template,
        "reasoning_level": settings.reasoning_level,
        "learning_default_enabled": settings.learning_default_enabled,
        "retrieval_candidate_limit": settings.retrieval_candidate_limit,
        "runtime_settings_refresh_seconds": settings.runtime_settings_refresh_seconds,
        "admin_cache_ttl_seconds": settings.admin_cache_ttl_seconds,
        "forget_confirm_ttl_seconds": settings.forget_confirm_ttl_seconds,
        "log_level": settings.log_level,
        "bot_admins_count": len(settings.bot_admin_ids),
        "control_plane": settings.control_plane_safe_config,
    }
    for key, value in visible.items():
        console.print(f"{key}: {value}")


@secrets_app.command("status")
def secrets_status() -> None:
    """Show safe master-key and encrypted-credential counts."""
    settings = get_settings()
    upgrade_schema(settings)
    try:
        status = run(build_secret_service(settings).status())
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"configured_key_ids: {','.join(status.configured_key_ids) or 'none'}")
    console.print(f"active_key_id: {status.active_key_id or 'none'}")
    console.print(f"encrypted_credentials: {status.encrypted_credentials}")
    for key_id, count in status.credentials_by_key_id.items():
        console.print(f"key_{key_id}: {count}")
    console.print(f"missing_key_ids: {','.join(status.missing_key_ids) or 'none'}")


@secrets_app.command("rewrap")
def secrets_rewrap(
    target_key_id: Annotated[str, typer.Option("--to", help="Configured target master-key ID.")],
    assume_yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Confirm that a current database backup exists and proceed non-interactively.",
        ),
    ] = False,
) -> None:
    """Atomically rewrap every managed credential DEK under another master key."""
    console.print(
        "[bold yellow]Back up the database before rewrapping provider credentials.[/bold yellow]"
    )
    if not assume_yes and not typer.confirm("Continue only after verifying the backup?"):
        raise typer.Abort()
    settings = get_settings()
    upgrade_schema(settings)
    try:
        count = run(build_secret_service(settings).rewrap(target_key_id))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Rewrapped {count} encrypted credentials to key {target_key_id}.")


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
    checks = control_plane_health_checks(settings)
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
        checks.append(("database", True, redact_url_credentials(settings.db_url)))
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


def control_plane_health_checks(settings: Settings) -> list[tuple[str, bool, str]]:
    if not settings.control_plane_enabled:
        return [("control_plane", True, "disabled")]
    safe = settings.control_plane_safe_config
    oidc_ready = bool(safe["oidc_client_configured"])
    master_ready = bool(safe["master_keys_configured"])
    session_ready = bool(settings.db_url.strip())
    pools_ready = bool(safe["provider_pools_enabled"])
    alert_enabled = bool(safe["alert_worker_enabled"])
    return [
        (
            "control_plane",
            bool(settings.control_plane_public_origin),
            "enabled with a validated public origin",
        ),
        (
            "control_plane_oidc",
            oidc_ready,
            "Telegram OIDC configured" if oidc_ready else "Telegram OIDC is incomplete",
        ),
        (
            "control_plane_master_keys",
            master_ready,
            (
                f"{len(settings.control_plane_master_key_ids)} key IDs configured; "
                "active ID present"
                if master_ready
                else "master keyring is incomplete"
            ),
        ),
        (
            "control_plane_session_store",
            session_ready,
            "database-backed revocable sessions configured",
        ),
        (
            "provider_pools",
            pools_ready,
            "database-managed pools; maximum "
            f"{settings.control_plane_max_credentials_per_profile} credentials per profile",
        ),
        (
            "alert_worker",
            True,
            "supervised in webhook mode" if alert_enabled else "inactive outside webhook mode",
        ),
    ]


if __name__ == "__main__":
    app()
