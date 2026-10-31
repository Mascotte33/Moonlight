"""disable_console_quick_edit: a click in the launcher console must not freeze the bot."""

from __future__ import annotations

from typing import Any

from moonlight.gui.app import disable_console_quick_edit

#: The cmd default: QuickEdit, insert, extended flags and the line-input bits.
CMD_DEFAULT_MODE = 0x01F7


class FakeKernel32:
    def __init__(self, mode: int | None) -> None:
        self.mode = mode  # None: no console attached
        self.set_modes: list[int] = []

    def GetStdHandle(self, which: int) -> int:  # noqa: N802 - Win32 name
        assert which == -10  # STD_INPUT_HANDLE
        return 7

    def GetConsoleMode(self, handle: int, mode_ref: Any) -> int:  # noqa: N802
        if self.mode is None:
            return 0
        mode_ref._obj.value = self.mode
        return 1

    def SetConsoleMode(self, handle: int, mode: int) -> int:  # noqa: N802
        self.set_modes.append(mode)
        return 1


def test_quick_edit_is_cleared_and_the_rest_kept() -> None:
    kernel32 = FakeKernel32(CMD_DEFAULT_MODE)
    assert disable_console_quick_edit(kernel32)
    assert kernel32.set_modes == [CMD_DEFAULT_MODE & ~0x0040 | 0x0080]


def test_no_console_changes_nothing() -> None:
    kernel32 = FakeKernel32(None)
    assert not disable_console_quick_edit(kernel32)
    assert kernel32.set_modes == []
