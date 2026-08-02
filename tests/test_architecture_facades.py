from __future__ import annotations

import ast
from pathlib import Path

import pytest

import pzp_ai_bot.db.repositories as repository_facade
from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.db.repos.canonical import CanonicalScopeSnapshotRepository
from pzp_ai_bot.db.repos.challenges import ForgetChallengeRepository
from pzp_ai_bot.db.repos.embeddings import EmbeddingRepository
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
from pzp_ai_bot.db.repos.messages import MessageRepository
from pzp_ai_bot.db.repos.policy import PolicyRepository
from pzp_ai_bot.db.repos.profiles import MemoryProfileRepository
from pzp_ai_bot.db.repos.settings import ScopeSettingsRepository
from pzp_ai_bot.memory.service import MemoryService
from pzp_ai_bot.telegram.commands import COMMAND_DESCRIPTIONS

EXPECTED_MEMORY_SERVICE_METHODS = {
    "allow_memory_profile",
    "approve_review",
    "ask",
    "ask_with_metadata",
    "audit_events",
    "browse_items",
    "browse_items_page",
    "compact_memory",
    "confirm_scope_settings",
    "delete_memory_item",
    "delete_note",
    "edit_note",
    "export_memory",
    "export_memory_chunks",
    "forget",
    "forget_confirmed",
    "forget_token_hash",
    "get_scope_settings",
    "import_jsonl",
    "import_telegram_desktop_json",
    "ingest",
    "known_scopes",
    "learn_status",
    "list_memory_profiles",
    "list_notes",
    "list_runtime_settings",
    "list_summary_templates",
    "list_system_prompt_templates",
    "memory_item",
    "memory_item_sources",
    "memory_status",
    "pending_review_scope",
    "pending_reviews",
    "pending_reviews_page",
    "preview_scope_settings",
    "prune_retention",
    "rebuild_daily_projection",
    "refresh_memory_item",
    "refresh_runtime_settings",
    "reject_review",
    "remember",
    "runtime_default",
    "runtime_setting",
    "search",
    "search_with_metadata",
    "set_allow_admins",
    "set_learning",
    "set_memory_profile",
    "set_runtime_settings",
    "set_scope_metadata",
    "set_scope_access_filter",
    "set_scope_settings",
    "set_summary_template",
    "set_system_prompt_template",
    "set_user_policy",
    "settings_token_hash",
    "skipped_messages",
    "stats",
    "summarize_idle",
    "summarize_now",
    "summarize_range",
}

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

EXPECTED_ROUTER_HANDLER_ORDER = [
    "complete_chat_enrollment",
    "ask",
    "memory_search",
    "help_command",
    "memory_export",
    "stats",
    "memory_status",
    "memory_scopes",
    "memory_debug",
    "audit_events",
    "runtime_settings",
    "learn_on",
    "learn_off",
    "learn_status",
    "memory_settings",
    "settings",
    "memory_prune",
    "allow_chat",
    "deny_chat",
    "allow_topic",
    "deny_topic",
    "allow_user",
    "deny_user",
    "allow_admins",
    "remember",
    "memory_items",
    "memory_item_delete",
    "memory_item_refresh",
    "memory_notes",
    "memory_note_edit",
    "memory_note_delete",
    "summarize_now",
    "memory_reviews",
    "memory_approve",
    "memory_reject",
    "memory_profiles",
    "memory_profile_set",
    "memory_profile_allow",
    "memory_templates",
    "memory_template_set",
    "system_prompts",
    "system_prompt_set",
    "summarize_range",
    "forget",
    "ingest",
]

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


def test_memory_service_public_facade_inventory_is_stable() -> None:
    public_methods = {
        name
        for name, value in MemoryService.__dict__.items()
        if callable(value) and not name.startswith("_")
    }

    assert public_methods == EXPECTED_MEMORY_SERVICE_METHODS


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
    from pzp_ai_bot.memory.provider_operations import (
        ProviderOperations,
        provider_call_telemetry,
        telemetry_scope,
    )
    from pzp_ai_bot.memory.service import provider_call_telemetry as facade_provider_call_telemetry
    from pzp_ai_bot.memory.service import telemetry_scope as facade_telemetry_scope

    assert ProviderOperations.__module__ == "pzp_ai_bot.memory.provider_operations"
    assert facade_provider_call_telemetry is provider_call_telemetry
    assert facade_telemetry_scope is telemetry_scope


def test_memory_settings_collaborator_owns_configuration_workflows(context: AppContext) -> None:
    from pzp_ai_bot.memory.settings_profiles import MemorySettingsService

    assert isinstance(context.memory._settings_profiles, MemorySettingsService)
    assert MemorySettingsService.__module__ == "pzp_ai_bot.memory.settings_profiles"
    assert context.memory.runtime_setting(
        "retrieval_top_k"
    ) == context.memory._settings_profiles.runtime_setting("retrieval_top_k")


def test_memory_retrieval_collaborator_owns_query_workflows(context: AppContext) -> None:
    from pzp_ai_bot.memory.retrieval_service import MemoryRetrievalService

    assert isinstance(context.memory._retrieval_service, MemoryRetrievalService)
    assert MemoryRetrievalService.__module__ == "pzp_ai_bot.memory.retrieval_service"


def test_memory_mutation_collaborator_owns_review_and_retention_workflows(
    context: AppContext,
) -> None:
    from pzp_ai_bot.memory.mutation_service import MemoryMutationService

    assert isinstance(context.memory._mutation_service, MemoryMutationService)
    assert MemoryMutationService.__module__ == "pzp_ai_bot.memory.mutation_service"


def test_memory_summary_collaborator_owns_summary_and_compaction_workflows(
    context: AppContext,
) -> None:
    from pzp_ai_bot.memory.summary_service import MemorySummaryService

    assert isinstance(context.memory._summary_service, MemorySummaryService)
    assert MemorySummaryService.__module__ == "pzp_ai_bot.memory.summary_service"


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


@pytest.mark.asyncio
async def test_router_family_facade_preserves_handler_order(context: AppContext) -> None:
    from pzp_ai_bot.telegram.router import build_router
    from pzp_ai_bot.telegram.router import export_document_stream as facade_export_document_stream
    from pzp_ai_bot.telegram.router import export_documents as facade_export_documents
    from pzp_ai_bot.telegram.routers.learning import (
        register_learning_policy_handlers,
        register_learning_status_handlers,
    )
    from pzp_ai_bot.telegram.routers.memory import (
        register_forget_handler,
        register_ingest_handler,
        register_memory_item_handlers,
        register_review_handlers,
    )
    from pzp_ai_bot.telegram.routers.operations import register_operations_handlers
    from pzp_ai_bot.telegram.routers.retrieval import (
        export_document_stream,
        export_documents,
        register_query_handlers,
        register_range_summary_handler,
        register_summarization_handlers,
    )
    from pzp_ai_bot.telegram.routers.settings import (
        register_profile_template_handlers,
        register_scope_settings_handlers,
    )
    from pzp_ai_bot.telegram.routers.shared import RouterGuards

    router = build_router(context)

    assert callable(register_learning_status_handlers)
    assert callable(register_learning_policy_handlers)
    assert callable(register_operations_handlers)
    assert callable(register_scope_settings_handlers)
    assert callable(register_profile_template_handlers)
    assert callable(register_query_handlers)
    assert callable(register_summarization_handlers)
    assert callable(register_range_summary_handler)
    assert callable(register_memory_item_handlers)
    assert callable(register_review_handlers)
    assert callable(register_forget_handler)
    assert callable(register_ingest_handler)
    assert facade_export_documents is export_documents
    assert facade_export_document_stream is export_document_stream
    assert RouterGuards.__module__ == "pzp_ai_bot.telegram.routers.shared"
    assert [
        getattr(handler.callback, "__name__", None)
        for handler in router.observers["message"].handlers
    ] == EXPECTED_ROUTER_HANDLER_ORDER


def test_registered_telegram_command_names_remain_unique_and_ordered() -> None:
    names = [name for name, _description in COMMAND_DESCRIPTIONS]

    assert len(names) == len(set(names))
    assert names[0:3] == ["ask", "memory_search", "memory_export"]
    assert names[-3:] == ["summarize_now", "forget", "help"]
