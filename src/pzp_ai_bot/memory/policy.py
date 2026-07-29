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


@dataclass(frozen=True, slots=True)
class PolicyRule:
    policy_type: str
    enabled: bool
    chat_id: int | None
    message_thread_id: int | None
    user_id: int | None


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    rules: tuple[PolicyRule, ...]

    @classmethod
    def from_policies(cls, policies: list[LearningPolicy]) -> PolicySnapshot:
        return cls(
            tuple(
                PolicyRule(
                    policy_type=policy.policy_type,
                    enabled=policy.enabled,
                    chat_id=policy.chat_id,
                    message_thread_id=policy.message_thread_id,
                    user_id=policy.user_id,
                )
                for policy in policies
            )
        )

    def scope_enabled(self, scope: Scope, *, default: bool) -> bool:
        topic = self._find("topic_learning", scope)
        if scope.thread_id is not None and topic is not None:
            return topic.enabled
        chat = self._find("chat_learning", Scope(scope.chat_id, None))
        if chat is not None:
            return chat.enabled
        global_policy = self._find("global_learning", Scope.global_scope())
        if global_policy is not None:
            return global_policy.enabled
        return default

    def user_denied(self, scope: Scope, user_id: int) -> bool:
        return any(
            rule.enabled
            and rule.policy_type == "user_deny"
            and rule.user_id == user_id
            and _policy_matches_scope(rule, scope)
            for rule in self.rules
        )

    def user_allowed(self, scope: Scope, user_id: int) -> bool:
        return any(
            rule.enabled
            and rule.policy_type == "user_allow"
            and rule.user_id == user_id
            and _policy_matches_scope(rule, scope)
            for rule in self.rules
        )

    def has_allowlist(self, scope: Scope) -> bool:
        return any(
            rule.enabled and rule.policy_type == "user_allow" and _policy_matches_scope(rule, scope)
            for rule in self.rules
        )

    def admins_allowed(self, scope: Scope) -> bool:
        return any(
            rule.enabled
            and rule.policy_type == "allow_admins"
            and _policy_matches_scope(rule, scope)
            for rule in self.rules
        )

    def _find(self, policy_type: str, scope: Scope) -> PolicyRule | None:
        for rule in self.rules:
            if rule.policy_type == policy_type and _same_scope(rule, scope):
                return rule
        return None


class PolicyService:
    """Evaluates chat/topic/user learning policy for a single DB session."""

    def __init__(
        self,
        settings: Settings,
        repository: PolicyRepository,
        *,
        learning_default_enabled: bool | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.learning_default_enabled = learning_default_enabled

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

    async def load_snapshot(self) -> PolicySnapshot:
        return PolicySnapshot.from_policies(await self.repository.list_policies())

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
        snapshot = await self.load_snapshot()
        if not snapshot.scope_enabled(scope, default=self._learning_default_enabled()):
            return LearnDecision(False, "learning_disabled")
        if user_id is None:
            return LearnDecision(False, "missing_user")
        if snapshot.user_denied(scope, user_id):
            return LearnDecision(False, "user_denied")
        if snapshot.has_allowlist(scope):
            if snapshot.user_allowed(scope, user_id):
                return LearnDecision(True, "allowed")
            if snapshot.admins_allowed(scope) and is_chat_admin:
                return LearnDecision(True, "allowed_admin")
            return LearnDecision(False, "user_not_allowed")
        if snapshot.admins_allowed(scope):
            return LearnDecision(True, "allowed_admin" if is_chat_admin else "allowed")
        return LearnDecision(True, "allowed")

    async def status(
        self, scope: Scope, *, snapshot: PolicySnapshot | None = None
    ) -> dict[str, bool | str]:
        effective_snapshot = snapshot if snapshot is not None else await self.load_snapshot()
        enabled = effective_snapshot.scope_enabled(scope, default=self._learning_default_enabled())
        admins = effective_snapshot.admins_allowed(scope)
        return {"scope": scope.key, "learning_enabled": enabled, "allow_admins": admins}

    def _learning_default_enabled(self) -> bool:
        if self.learning_default_enabled is not None:
            return self.learning_default_enabled
        return self.settings.learning_default_enabled


def _same_scope(policy: PolicyRule, scope: Scope) -> bool:
    return policy.chat_id == scope.chat_id and policy.message_thread_id == scope.thread_id


def _policy_matches_scope(policy: PolicyRule, scope: Scope) -> bool:
    if policy.chat_id is None:
        return True
    if policy.chat_id != scope.chat_id:
        return False
    if policy.message_thread_id is None:
        return True
    return policy.message_thread_id == scope.thread_id
