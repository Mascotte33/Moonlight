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
    #: A ``check`` also jumps with fewer health / mana potions than this
    #: on the action bar (0 = off).
    health_potions_below: int = 0
    mana_potions_below: int = 0
