from __future__ import annotations

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
from pzp_ai_bot.memory.service import MemoryService
from pzp_ai_bot.telegram.commands import COMMAND_DESCRIPTIONS

EXPECTED_MEMORY_SERVICE_METHODS = {
    "allow_memory_profile",
    "approve_review",
    "ask",
    "audit_events",
    "browse_items",
    "compact_memory",
    "confirm_scope_settings",
    "delete_memory_item",
    "delete_note",
    "edit_note",
    "export_memory",
    "export_memory_chunks",
    "forget",
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
    "memory_status",
    "pending_review_scope",
    "pending_reviews",
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
    "set_allow_admins",
    "set_learning",
    "set_memory_profile",
    "set_runtime_settings",
    "set_scope_metadata",
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


def test_registered_telegram_command_names_remain_unique_and_ordered() -> None:
    names = [name for name, _description in COMMAND_DESCRIPTIONS]

    assert len(names) == len(set(names))
    assert names[0:3] == ["ask", "memory_search", "memory_export"]
    assert names[-3:] == ["summarize_now", "forget", "help"]
