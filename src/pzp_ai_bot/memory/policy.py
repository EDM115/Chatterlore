from __future__ import annotations

from dataclasses import dataclass

from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import LearningPolicy
from pzp_ai_bot.db.repositories import PolicyRepository
from pzp_ai_bot.memory.scope import Scope


@dataclass(frozen=True, slots=True)
class LearnDecision:
    allowed: bool
    reason: str


class PolicyService:
    """Evaluates chat/topic/user learning policy for a single DB session."""

    def __init__(self, settings: Settings, repository: PolicyRepository) -> None:
        self.settings = settings
        self.repository = repository

    async def set_learning(
        self, scope: Scope | None, enabled: bool, updated_by: int | None = None
    ) -> None:
        policy_type = (
            "global_learning"
            if scope is None or scope.is_global
            else ("topic_learning" if scope.thread_id is not None else "chat_learning")
        )
        await self.repository.set_policy(
            policy_type=policy_type, enabled=enabled, scope=scope, updated_by=updated_by
        )

    async def set_user(
        self, scope: Scope | None, user_id: int, allowed: bool, updated_by: int | None = None
    ) -> None:
        await self.repository.set_policy(
            policy_type="user_allow" if allowed else "user_deny",
            enabled=True,
            scope=scope,
            user_id=user_id,
            updated_by=updated_by,
        )

    async def set_allow_admins(
        self, scope: Scope | None, enabled: bool, updated_by: int | None = None
    ) -> None:
        await self.repository.set_policy(
            policy_type="allow_admins", enabled=enabled, scope=scope, updated_by=updated_by
        )

    async def decide(
        self,
        *,
        scope: Scope,
        user_id: int | None,
        is_bot: bool,
        is_command: bool,
        text: str,
        is_chat_admin: bool = False,
    ) -> LearnDecision:
        if is_bot:
            return LearnDecision(False, "bot_message")
        if is_command:
            return LearnDecision(False, "command")
        if not text.strip():
            return LearnDecision(False, "empty_text")
        if not await self._scope_enabled(scope):
            return LearnDecision(False, "learning_disabled")
        if user_id is None:
            return LearnDecision(False, "missing_user")
        if await self._user_denied(scope, user_id):
            return LearnDecision(False, "user_denied")
        if await self._has_allowlist(scope):
            if await self._user_allowed(scope, user_id):
                return LearnDecision(True, "allowed")
            if await self._admins_allowed(scope) and is_chat_admin:
                return LearnDecision(True, "allowed_admin")
            return LearnDecision(False, "user_not_allowed")
        if await self._admins_allowed(scope):
            return LearnDecision(True, "allowed_admin" if is_chat_admin else "allowed")
        return LearnDecision(True, "allowed")

    async def status(self, scope: Scope) -> dict[str, bool | str]:
        enabled = await self._scope_enabled(scope)
        admins = await self._admins_allowed(scope)
        return {"scope": scope.key, "learning_enabled": enabled, "allow_admins": admins}

    async def _scope_enabled(self, scope: Scope) -> bool:
        policies = await self.repository.list_policies()
        topic = _find_policy(policies, "topic_learning", scope)
        if scope.thread_id is not None and topic is not None:
            return topic.enabled
        chat = _find_policy(policies, "chat_learning", Scope(scope.chat_id, None))
        if chat is not None:
            return chat.enabled
        global_policy = _find_policy(policies, "global_learning", Scope.global_scope())
        if global_policy is not None:
            return global_policy.enabled
        return self.settings.learning_default_enabled

    async def _user_denied(self, scope: Scope, user_id: int) -> bool:
        policies = await self.repository.list_policies()
        return any(
            p.enabled
            and p.policy_type == "user_deny"
            and p.user_id == user_id
            and _policy_matches_scope(p, scope)
            for p in policies
        )

    async def _user_allowed(self, scope: Scope, user_id: int) -> bool:
        policies = await self.repository.list_policies()
        return any(
            p.enabled
            and p.policy_type == "user_allow"
            and p.user_id == user_id
            and _policy_matches_scope(p, scope)
            for p in policies
        )

    async def _has_allowlist(self, scope: Scope) -> bool:
        policies = await self.repository.list_policies()
        return any(
            p.enabled and p.policy_type == "user_allow" and _policy_matches_scope(p, scope)
            for p in policies
        )

    async def _admins_allowed(self, scope: Scope) -> bool:
        policies = await self.repository.list_policies()
        return any(
            p.enabled and p.policy_type == "allow_admins" and _policy_matches_scope(p, scope)
            for p in policies
        )


def _find_policy(
    policies: list[LearningPolicy], policy_type: str, scope: Scope
) -> LearningPolicy | None:
    for policy in policies:
        if policy.policy_type == policy_type and _same_scope(policy, scope):
            return policy
    return None


def _same_scope(policy: LearningPolicy, scope: Scope) -> bool:
    return policy.chat_id == scope.chat_id and policy.message_thread_id == scope.thread_id


def _policy_matches_scope(policy: LearningPolicy, scope: Scope) -> bool:
    if policy.chat_id is None:
        return True
    if policy.chat_id != scope.chat_id:
        return False
    if policy.message_thread_id is None:
        return True
    return policy.message_thread_id == scope.thread_id
