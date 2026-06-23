from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from pzp_ai_bot.app_context import build_context
from pzp_ai_bot.config import Settings, get_settings
from pzp_ai_bot.db.session import upgrade_schema
from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.memory.service import parse_memory_export_format
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
        "bot_admins_count": len(settings.bot_admin_ids),
    }
    for key, value in visible.items():
        console.print(f"{key}: {value}")


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
