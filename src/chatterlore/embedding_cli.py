from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from chatterlore.app_context import AppContext, build_context
from chatterlore.config import get_settings
from chatterlore.embedding_adoption import LegacyAdoptionManifest
from chatterlore.memory.embedding_control_errors import control_error_code
from chatterlore.memory.local_preparation import prepare_local_model
from chatterlore.memory.scope import parse_scope

embedding_index_app = typer.Typer(help="Preview, build and explicitly activate embedding indexes.")
local_model_app = typer.Typer(help="Explicitly prepare configured embedded local models.")


def _execute(operation: Callable[[AppContext], Awaitable[dict[str, Any]]]) -> None:
    async def execute() -> dict[str, Any]:
        context = await build_context(get_settings())
        try:
            return await operation(context)
        finally:
            try:
                await context.memory.provider_registry.aclose()
            finally:
                await context.session_factory.kw["bind"].dispose()

    try:
        result = asyncio.run(execute())
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise typer.BadParameter(
            f"Request could not be completed safely ({control_error_code(exc)})"
        ) from None
    typer.echo(json.dumps(result, ensure_ascii=False, default=str))


@embedding_index_app.command("status")
def status(scope: str = "all", job_id: Annotated[int | None, typer.Option(min=1)] = None) -> None:
    """Read index metadata without embedding calls or model downloads."""
    parsed = parse_scope(scope)
    _execute(
        lambda context: (
            context.memory.embedding_indexes.get_job(job_id)
            if job_id is not None
            else context.memory.embedding_indexes.status(parsed)
        )
    )


def _legacy_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("manifest limit exceeded")
        return LegacyAdoptionManifest.parse(json.loads(raw.decode("utf-8"))).model_dump(mode="json")
    except (OSError, ValueError, RecursionError):
        raise typer.BadParameter(
            "The manifest is invalid (embedding_adoption_manifest_invalid)"
        ) from None


@embedding_index_app.command("adopt-preview")
def adopt_preview(manifest: Annotated[Path, typer.Option("--manifest")]) -> None:
    """Validate original provenance for at most 100 explicit legacy vector mappings."""
    payload = _legacy_manifest(manifest)
    _execute(lambda context: context.memory.embedding_indexes.preview_legacy_adoption(payload))


@embedding_index_app.command("adopt")
def adopt_legacy(
    manifest: Annotated[Path, typer.Option("--manifest")],
    preview_hash: Annotated[str, typer.Option("--preview-hash")],
    confirmation: Annotated[str, typer.Option("--confirmation")],
    attest_provenance: Annotated[bool, typer.Option("--attest-provenance")] = False,
) -> None:
    """Adopt only explicitly evidenced vectors after preview, attestation and exact confirmation."""
    if not attest_provenance or confirmation != f"ADOPT {preview_hash}":
        raise typer.BadParameter(
            "Confirmation/attestation required (embedding_adoption_confirmation_required)"
        )
    payload = _legacy_manifest(manifest)
    _execute(
        lambda context: context.memory.embedding_indexes.adopt_legacy(
            payload, preview_hash=preview_hash, confirmation=confirmation, provenance_attested=True
        )
    )


@embedding_index_app.command("preview")
def preview(
    profile: Annotated[str, typer.Option("--profile")], scope: str = "all", model: str | None = None
) -> None:
    """Inspect eligible source counts and costs without starting work."""
    parsed = parse_scope(scope)
    _execute(lambda context: context.memory.embedding_indexes.preview(parsed, profile, model=model))


@embedding_index_app.command("start")
def start(
    profile: Annotated[str, typer.Option("--profile")], scope: str = "all", model: str | None = None
) -> None:
    """Create an explicit resumable index job."""
    parsed = parse_scope(scope)
    _execute(
        lambda context: context.memory.embedding_indexes.create_job(
            parsed, profile, model=model, actor_user_id=None
        )
    )


@embedding_index_app.command("run")
def run_job(
    job_id: Annotated[int, typer.Argument(min=1)],
    max_batches: Annotated[int, typer.Option(min=1, max=10)] = 1,
) -> None:
    """Run at most ten bounded batches, which may use paid providers."""
    _execute(
        lambda context: context.memory.embedding_indexes.run_job(job_id, max_batches=max_batches)
    )


@embedding_index_app.command("resume")
def resume(
    job_id: Annotated[int, typer.Argument(min=1)], version: Annotated[int, typer.Option(min=1)]
) -> None:
    """Resume a job only at its current optimistic version."""
    _execute(
        lambda context: context.memory.embedding_indexes.resume_job(
            job_id, version=version, actor_user_id=None
        )
    )


@embedding_index_app.command("cancel")
def cancel(
    job_id: Annotated[int, typer.Argument(min=1)], version: Annotated[int, typer.Option(min=1)]
) -> None:
    """Cancel a job only at its current optimistic version."""
    _execute(
        lambda context: context.memory.embedding_indexes.cancel_job(
            job_id, version=version, actor_user_id=None
        )
    )


@embedding_index_app.command("activate")
def activate(
    job_id: Annotated[int, typer.Argument(min=1)],
    version: Annotated[int, typer.Option(min=1)],
    confirmation: Annotated[str, typer.Option(help="Exact phrase: ACTIVATE <job-id>.")],
) -> None:
    """Atomically activate a complete current index after confirmation."""
    if confirmation != f"ACTIVATE {job_id}":
        raise typer.BadParameter(f"confirmation must be ACTIVATE {job_id}")
    _execute(
        lambda context: context.memory.embedding_indexes.activate_job(
            job_id, version=version, confirmation=confirmation, actor_user_id=None
        )
    )


@local_model_app.command("prepare")
def prepare(
    profile: Annotated[str, typer.Option("--profile")],
    mode: Annotated[str, typer.Option(help="chat or embedding.")],
    allow_download: Annotated[
        bool, typer.Option(help="Explicitly authorize a bounded safe model download.")
    ] = False,
) -> None:
    """Load cached artifacts, or explicitly fetch permitted small artifacts."""
    if mode not in {"chat", "embedding"}:
        raise typer.BadParameter("mode must be chat or embedding")
    _execute(
        lambda context: prepare_local_model(
            context, profile, mode=mode, allow_download=allow_download
        )
    )
