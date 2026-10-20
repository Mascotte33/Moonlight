"""moonlight.gui — the customtkinter presentation layer (T39).

Importing this package has no side effects: the window is only created by
:class:`moonlight.gui.app.GuiApp` (``python -m moonlight.gui``). The GUI is
a thin presentation/control layer over the already-tested bot modules —
it edits config and displays live :class:`moonlight.state.WorldState`
snapshots.

The "Start bot" action builds the real loop through
:func:`moonlight.cli.build_loop` (see :mod:`moonlight.gui.app`): real screen
capture, the focus gate and every enabled module. A missing Arduino requires
an explicit "simulation" toggle — there is no silent ``FakeInput`` fallback
(F-13). For headless/supervised runs use the CLI (``moonlight run``).
"""

from moonlight.gui.state_bridge import (
    BotController,
    GuiContext,
    HardwareController,
    LogFeed,
)

__all__ = [
    "BotController",
    "GuiContext",
    "HardwareController",
    "LogFeed",
]
