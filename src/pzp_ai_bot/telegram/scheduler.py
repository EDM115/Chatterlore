from __future__ import annotations

import asyncio
from contextlib import suppress

from pzp_ai_bot.app_context import AppContext


async def idle_summarizer_loop(context: AppContext, *, interval_seconds: int | None = None) -> None:
    while True:
        interval = (
            interval_seconds
            if interval_seconds is not None
            else min(int(context.memory.runtime_setting("summary_idle_seconds")), 60)
        )
        await asyncio.sleep(max(1, interval))
        await context.memory.summarize_idle()


async def runtime_settings_refresher_loop(context: AppContext) -> None:
    while True:
        await asyncio.sleep(max(1, context.settings.runtime_settings_refresh_seconds))
        await context.memory.refresh_runtime_settings()


def start_runtime_settings_refresher(context: AppContext) -> asyncio.Task[None]:
    return asyncio.create_task(
        runtime_settings_refresher_loop(context), name="pzp-runtime-settings-refresher"
    )


def start_idle_summarizer(context: AppContext) -> asyncio.Task[None]:
    return asyncio.create_task(idle_summarizer_loop(context), name="pzp-idle-summarizer")


async def stop_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def stop_idle_summarizer(task: asyncio.Task[None]) -> None:
    await stop_task(task)
