from __future__ import annotations

import ast
from pathlib import Path

import pzp_ai_bot.db.repositories as repository_facade
from pzp_ai_bot.db.repos.canonical import CanonicalScopeSnapshotRepository
from pzp_ai_bot.db.repos.challenges import ForgetChallengeRepository
from pzp_ai_bot.db.repos.embeddings import EmbeddingRepository
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
from pzp_ai_bot.db.repos.messages import MessageRepository
from pzp_ai_bot.db.repos.policy import PolicyRepository
from pzp_ai_bot.db.repos.profiles import MemoryProfileRepository
from pzp_ai_bot.db.repos.settings import ScopeSettingsRepository
from pzp_ai_bot.telegram.commands import COMMAND_DESCRIPTIONS

EXPECTED_REPOSITORY_EXPORTS = {
    "AIUsageStats",
    "CanonicalScopeSnapshotRecord",
    "CanonicalScopeSnapshotRepository",
    "EffectiveScopeSettings",
    "EmbeddingRepository",
    "EventRepository",
    "ForgetChallengeRepository",
    "ForgetChallengeView",
    "KnowledgeRepository",
    "KnowledgeScopeCounts",
    "MemoryProfileRepository",
    "MemoryProfileView",
    "MemoryReviewRepository",
    "MessageRepository",
    "MessageScopeCounts",
    "PolicyRepository",
    "RetrievedItem",
    "RuntimeSettingRepository",
    "RuntimeSettingView",
    "ScopeMetadataRepository",
    "ScopeSettingsRepository",
    "SettingsChallengeRepository",
    "SettingsChallengeView",
    "SourceReference",
    "SummaryTemplateRepository",
    "SystemPromptTemplateRepository",
    "build_telegram_message_url",
    "dedupe_scopes",
    "exact_scope_filter",
    "forget_challenge_view",
    "normalize_profile_name",
    "runtime_setting_view",
    "scope_filter",
    "settings_challenge_view",
    "utc_now",
}


COLLABORATOR_MODULES = [
    "mutation_service.py",
    "provider_operations.py",
    "retrieval_service.py",
    "settings_profiles.py",
    "summary_service.py",
    "transfer.py",
]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_repository_compatibility_facade_exports_are_stable() -> None:
    assert vars(repository_facade).keys() >= EXPECTED_REPOSITORY_EXPORTS
    assert repository_facade.MessageRepository is MessageRepository
    assert repository_facade.KnowledgeRepository is KnowledgeRepository
    assert repository_facade.CanonicalScopeSnapshotRepository is CanonicalScopeSnapshotRepository
    assert repository_facade.EmbeddingRepository is EmbeddingRepository
    assert repository_facade.PolicyRepository is PolicyRepository
    assert repository_facade.ScopeSettingsRepository is ScopeSettingsRepository
    assert repository_facade.MemoryProfileRepository is MemoryProfileRepository
    assert repository_facade.ForgetChallengeRepository is ForgetChallengeRepository
    assert repository_facade.EventRepository is EventRepository


def test_provider_operation_helpers_remain_available_from_service_facade() -> None:
    from pzp_ai_bot.memory.provider_operations import provider_call_telemetry, telemetry_scope
    from pzp_ai_bot.memory.service import provider_call_telemetry as facade_provider_call_telemetry
    from pzp_ai_bot.memory.service import telemetry_scope as facade_telemetry_scope

    assert facade_provider_call_telemetry is provider_call_telemetry
    assert facade_telemetry_scope is telemetry_scope


def test_lower_layers_do_not_import_telegram_or_back_import_service() -> None:
    source_root = Path(__file__).parents[1] / "src" / "pzp_ai_bot"
    lower_layer_files = [
        *sorted((source_root / "db").rglob("*.py")),
        *sorted((source_root / "memory").rglob("*.py")),
    ]
    telegram_imports = {
        f"{path.relative_to(source_root)}: {module}"
        for path in lower_layer_files
        for module in imported_modules(path)
        if module == "pzp_ai_bot.telegram" or module.startswith("pzp_ai_bot.telegram.")
    }
    collaborator_back_imports = {
        f"{name}: {module}"
        for name in COLLABORATOR_MODULES
        for module in imported_modules(source_root / "memory" / name)
        if module == "pzp_ai_bot.memory.service"
    }

    assert telegram_imports == set()
    assert collaborator_back_imports == set()


def test_router_facade_preserves_export_helpers() -> None:
    from pzp_ai_bot.telegram.router import export_document_stream as facade_export_document_stream
    from pzp_ai_bot.telegram.router import export_documents as facade_export_documents
    from pzp_ai_bot.telegram.routers.retrieval import export_document_stream, export_documents

    assert facade_export_documents is export_documents
    assert facade_export_document_stream is export_document_stream


def test_registered_telegram_command_names_remain_unique() -> None:
    names = [name for name, _description in COMMAND_DESCRIPTIONS]

    assert len(names) == len(set(names))


def test_control_plane_extracted_dependencies_preserve_public_identity_without_back_imports() -> (
    None
):
    import importlib

    from pzp_ai_bot.control_plane import app as facade

    common = importlib.import_module("pzp_ai_bot.control_plane.api.common")
    auth = importlib.import_module("pzp_ai_bot.control_plane.api.auth")
    memory = importlib.import_module("pzp_ai_bot.control_plane.api.memory")
    assert facade.ControlPlaneAPIError is common.ControlPlaneAPIError
    assert facade.DevelopmentAuthConfig is auth.DevelopmentAuthConfig
    assert facade._memory_export_stream is memory._memory_export_stream
    source_root = Path(__file__).parents[1] / "src" / "pzp_ai_bot" / "control_plane"
    for path in [*(source_root / "api").glob("*.py"), source_root / "embedding_routes.py"]:
        assert "pzp_ai_bot.control_plane.app" not in imported_modules(path)
