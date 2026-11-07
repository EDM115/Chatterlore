from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from pzp_ai_bot.app_context import AppContext

logger = structlog.get_logger(__name__)

IDLE_SUMMARIZER_TASK_NAME = "pzp-idle-summarizer"
RUNTIME_SETTINGS_REFRESHER_TASK_NAME = "pzp-runtime-settings-refresher"
ADMIN_ALERT_DELIVERY_TASK_NAME = "pzp-admin-alert-delivery"
EMBEDDING_INDEX_TASK_NAME = "pzp-embedding-index-worker"


@dataclass(slots=True)
class BackgroundTaskState:
    name: str
    running: bool = False
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_exception_type: str | None = None
    consecutive_failures: int = 0


@dataclass(frozen=True, slots=True)
class BackgroundTaskHandle:
    task: asyncio.Task[None]
    state: BackgroundTaskState

    @property
    def name(self) -> str:
        return self.state.name

    @property
    def healthy(self) -> bool:
        return self.state.running and not self.task.done()


async def idle_summarizer_loop(
    context: AppContext,
    *,
    interval_seconds: int | None = None,
    state: BackgroundTaskState | None = None,
) -> None:
    task_state = state or BackgroundTaskState(name=IDLE_SUMMARIZER_TASK_NAME)

    def interval() -> int:
        configured = (
            interval_seconds
            if interval_seconds is not None
            else min(int(context.memory.runtime_setting("summary_idle_seconds")), 60)
        )
        return max(1, configured)

    await _supervised_loop(
        state=task_state, interval=interval, operation=context.memory.summarize_idle
    )


async def runtime_settings_refresher_loop(
    context: AppContext, *, state: BackgroundTaskState | None = None
) -> None:
    task_state = state or BackgroundTaskState(name=RUNTIME_SETTINGS_REFRESHER_TASK_NAME)
    await _supervised_loop(
        state=task_state,
        interval=lambda: max(1, context.settings.runtime_settings_refresh_seconds),
        operation=context.memory.refresh_runtime_settings,
    )


async def admin_alert_delivery_loop(
    context: AppContext, bot: object, *, state: BackgroundTaskState | None = None
) -> None:
    task_state = state or BackgroundTaskState(name=ADMIN_ALERT_DELIVERY_TASK_NAME)

    async def deliver() -> object:
        if context.control_plane is None:
            return 0
        return await context.control_plane.alerts.deliver_pending(bot, now=datetime.now(UTC))

    await _supervised_loop(
        state=task_state,
        interval=lambda: context.settings.control_plane_alert_delivery_interval_seconds,
        operation=deliver,
    )


def start_embedding_index_worker(context: AppContext) -> BackgroundTaskHandle:
    state = BackgroundTaskState(name=EMBEDDING_INDEX_TASK_NAME)
    task = asyncio.create_task(
        _supervised_loop(
            state=state, interval=lambda: 2, operation=context.memory.embedding_indexes.worker_tick
        ),
        name=EMBEDDING_INDEX_TASK_NAME,
    )
    return BackgroundTaskHandle(task, state)


async def _supervised_loop(
    *,
    state: BackgroundTaskState,
    interval: Callable[[], int],
    operation: Callable[[], Awaitable[object]],
) -> None:
    state.running = True
    delay = interval()
    try:
        while True:
            await asyncio.sleep(delay)
            try:
                await operation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.last_failure_at = datetime.now(UTC)
                state.last_exception_type = type(exc).__name__
                state.consecutive_failures += 1
                backoff_seconds = min(interval(), 2 ** min(state.consecutive_failures - 1, 30))
                logger.warning(
                    "background_task_iteration_failed",
                    event_type="background_task_iteration_failed",
                    task=state.name,
                    exception_type=state.last_exception_type,
                    consecutive_failures=state.consecutive_failures,
                    backoff_seconds=backoff_seconds,
                )
                delay = backoff_seconds
            else:
                state.last_success_at = datetime.now(UTC)
                state.last_exception_type = None
                state.consecutive_failures = 0
                delay = interval()
    finally:
        state.running = False


def start_runtime_settings_refresher(context: AppContext) -> BackgroundTaskHandle:
    state = BackgroundTaskState(name=RUNTIME_SETTINGS_REFRESHER_TASK_NAME, running=True)
    task = asyncio.create_task(
        runtime_settings_refresher_loop(context, state=state), name=state.name
    )
    return BackgroundTaskHandle(task=task, state=state)


def start_idle_summarizer(context: AppContext) -> BackgroundTaskHandle:
    state = BackgroundTaskState(name=IDLE_SUMMARIZER_TASK_NAME, running=True)
    task = asyncio.create_task(idle_summarizer_loop(context, state=state), name=state.name)
    return BackgroundTaskHandle(task=task, state=state)


def start_admin_alert_delivery(context: AppContext, bot: object) -> BackgroundTaskHandle | None:
    if context.control_plane is None:
        return None
    state = BackgroundTaskState(name=ADMIN_ALERT_DELIVERY_TASK_NAME, running=True)
    task = asyncio.create_task(
        admin_alert_delivery_loop(context, bot, state=state), name=state.name
    )
    return BackgroundTaskHandle(task=task, state=state)


async def stop_task(handle: BackgroundTaskHandle | asyncio.Task[None]) -> None:
    task = handle.task if isinstance(handle, BackgroundTaskHandle) else handle
    state = handle.state if isinstance(handle, BackgroundTaskHandle) else None
    task_name = state.name if state is not None else task.get_name()
    try:
        if task.done():
            if task.cancelled():
                return
            exception = task.exception()
            logger.warning(
                "background_task_stopped_unexpectedly",
                event_type="background_task_stopped_unexpectedly",
                task=task_name,
                exception_type=type(exception).__name__ if exception is not None else None,
            )
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(
                "background_task_stopped_unexpectedly",
                event_type="background_task_stopped_unexpectedly",
                task=task_name,
                exception_type=type(exc).__name__,
            )
    finally:
        if state is not None:
            state.running = False


async def stop_idle_summarizer(handle: BackgroundTaskHandle | asyncio.Task[None]) -> None:
    await stop_task(handle)
