from __future__ import annotations

import pytest

from pzp_ai_bot.telegram.commands import COMMAND_DESCRIPTIONS, clear_bot_commands, command_help_text


class FakeBot:
    def __init__(self) -> None:
        self.commands = None
        self.deleted_default = False

    async def set_my_commands(self, commands) -> None:
        self.commands = commands

    async def delete_my_commands(self) -> None:
        self.deleted_default = True


def test_command_help_lists_supported_commands_without_requiring_menu_publication() -> None:
    help_text = command_help_text()
    command_names = {name for name, _description in COMMAND_DESCRIPTIONS}

    assert {"start", "help"} <= command_names
    assert "/start" in help_text
    assert "/ask" in help_text
    assert "/memory_export" in help_text
    assert "/forget" in help_text
    assert "[chat_provider=name]" in help_text
    assert "[summary_provider=name]" in help_text
    assert "[embedding_provider=name]" in help_text


@pytest.mark.asyncio
async def test_startup_clears_the_old_default_menu_without_publishing_commands() -> None:
    bot = FakeBot()

    await clear_bot_commands(bot)

    assert bot.deleted_default
    assert bot.commands is None
