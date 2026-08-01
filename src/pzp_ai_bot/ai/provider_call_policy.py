from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .failure_classification import ProviderFailureDecision, classify_provider_failure


class OpaqueCredentialLease(Protocol):
    @property
    def profile_revision(self) -> int: ...

    @property
    def label(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ProviderLeaseAttemptMetadata:
    profile_revision: int
    credential_fingerprint: str
    credential_label: str
    same_key_attempts: int
    final_classification: str


@dataclass(frozen=True, slots=True)
class ProviderLeaseCallResult[T]:
    value: T
    metadata: ProviderLeaseAttemptMetadata


class ProviderLeaseCallError(RuntimeError):
    def __init__(
        self,
        *,
        error: Exception,
        decision: ProviderFailureDecision,
        metadata: ProviderLeaseAttemptMetadata,
    ) -> None:
        super().__init__(f"provider lease call failed ({decision.classification.value})")
        self.original_error = error
        self.decision = decision
        self.metadata = metadata


class ProviderCallPolicy:
    def __init__(
        self, *, attempts: int, backoff_seconds: float, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.attempts = max(1, attempts)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.clock = clock or (lambda: datetime.now(UTC))

    async def execute[T, LeaseT: OpaqueCredentialLease](
        self, lease: LeaseT, operation: Callable[[LeaseT], Awaitable[T]]
    ) -> ProviderLeaseCallResult[T]:
        for attempt in range(1, self.attempts + 1):
            try:
                value = await operation(lease)
            except Exception as error:
                original = (
                    error.original_error if isinstance(error, ProviderLeaseCallError) else error
                )
                decision = classify_provider_failure(original, now=self.clock())
                metadata = ProviderLeaseAttemptMetadata(
                    profile_revision=lease.profile_revision,
                    credential_fingerprint=lease.fingerprint,
                    credential_label=lease.label,
                    same_key_attempts=attempt,
                    final_classification=decision.classification.value,
                )
                if not decision.same_key_retry or attempt == self.attempts:
                    raise ProviderLeaseCallError(
                        error=original, decision=decision, metadata=metadata
                    ) from error
                delay = self.backoff_seconds * (2 ** (attempt - 1))
                if delay > 0:
                    await asyncio.sleep(delay)
            else:
                return ProviderLeaseCallResult(
                    value=value,
                    metadata=ProviderLeaseAttemptMetadata(
                        profile_revision=lease.profile_revision,
                        credential_fingerprint=lease.fingerprint,
                        credential_label=lease.label,
                        same_key_attempts=attempt,
                        final_classification="success",
                    ),
                )
        raise RuntimeError("provider call policy reached an invalid state")


__all__ = [
    "OpaqueCredentialLease",
    "ProviderCallPolicy",
    "ProviderLeaseAttemptMetadata",
    "ProviderLeaseCallError",
    "ProviderLeaseCallResult",
]
