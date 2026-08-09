from __future__ import annotations

import pytest

from pzp_ai_bot.telegram.commands import BOT_COMMANDS, command_help_text, register_bot_commands


class FakeBot:
    def __init__(self) -> None:
        self.commands = None

    async def set_my_commands(self, commands) -> None:
        self.commands = commands


def test_command_help_lists_registered_commands() -> None:
    help_text = command_help_text()
    command_names = {command.command for command in BOT_COMMANDS}

    assert {"start", "help"} <= command_names
    assert "/start" in help_text
    assert "/ask" in help_text
    assert "/memory_export" in help_text
    assert "/forget" in help_text


@pytest.mark.asyncio
async def test_register_bot_commands_calls_bot_api() -> None:
    bot = FakeBot()

    await register_bot_commands(bot)

    assert bot.commands == BOT_COMMANDS
