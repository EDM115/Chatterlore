from __future__ import annotations

from chatterlore.telegram.commands import COMMAND_DESCRIPTIONS


def test_registered_telegram_command_names_remain_unique() -> None:
    names = [name for name, _description in COMMAND_DESCRIPTIONS]

    assert len(names) == len(set(names))
