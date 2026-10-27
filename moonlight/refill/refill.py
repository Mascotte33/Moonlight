"""Refill settings: when a ``check`` waypoint sends the bot to town.

The town trip itself is part of the waypoint list (``check``, ``say``,
``key``, ``drag``; see :mod:`moonlight.cavebot.cavebot`).
"""

# doc: docs/prs/refill.md#check-waypoint

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RefillConfig"]


@dataclass(frozen=True)
class RefillConfig:
    #: Used capacity % at which a ``check`` waypoint jumps to its label.
    trigger_cap: float = 90.0
