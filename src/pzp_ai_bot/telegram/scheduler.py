from __future__ import annotations

import asyncio
from contextlib import suppress

from pzp_ai_bot.app_context import AppContext


async def idle_summarizer_loop(context: AppContext, *, interval_seconds: int | None = None) -> None:
    interval = (
        interval_seconds
        if interval_seconds is not None
        else min(context.settings.summary_idle_seconds, 60)
    )
    while True:
        await asyncio.sleep(max(1, interval))
        await context.memory.summarize_idle()


def start_idle_summarizer(context: AppContext) -> asyncio.Task[None]:
    return asyncio.create_task(idle_summarizer_loop(context), name="pzp-idle-summarizer")


async def stop_idle_summarizer(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
