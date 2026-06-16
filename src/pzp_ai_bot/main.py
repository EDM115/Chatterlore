from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from pzp_ai_bot.app_context import build_context
from pzp_ai_bot.config import get_settings
from pzp_ai_bot.db.session import create_schema
from pzp_ai_bot.memory.scope import Scope, parse_scope
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
    run(create_schema(settings))
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
    parsed = parse_scope(scope, current=Scope.global_scope())
    count = run(context.memory.summarize_now(parsed))
    run(context.memory.compact_memory())
    console.print(f"Summarized {count} pending messages for {parsed.key}.")


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
    if memory_format not in {"md", "jsonl"}:
        raise typer.BadParameter("format must be md or jsonl")
    settings = get_settings()
    context = run(build_context(settings))
    parsed = parse_scope(scope, current=Scope.global_scope())
    payload = run(
        context.memory.export_memory(None if parsed.is_global else parsed, fmt=memory_format)
    )  # type: ignore[arg-type]
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
    count = run(context.memory.import_jsonl(file, scope))
    console.print(f"Imported {count} memory items into {scope.key}.")


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
        "learning_default_enabled": settings.learning_default_enabled,
        "bot_admins_count": len(settings.bot_admin_ids),
    }
    for key, value in visible.items():
        console.print(f"{key}: {value}")


if __name__ == "__main__":
    app()
