"""moonlight.gui — the customtkinter presentation layer (T39).

Importing this package has no side effects: the window is only created by
:class:`moonlight.gui.app.GuiApp` (``python -m moonlight.gui``). The GUI is
a thin presentation/control layer over the already-tested bot modules —
it edits config, drives the bot loop through
:class:`moonlight.gui.state_bridge.BotController`, and displays live
:class:`moonlight.state.WorldState` snapshots.
"""

from moonlight.gui.state_bridge import (
    BotController,
    GuiContext,
    HardwareController,
    LogFeed,
    PerceptionWriter,
)

__all__ = [
    "BotController",
    "GuiContext",
    "HardwareController",
    "LogFeed",
    "PerceptionWriter",
]
