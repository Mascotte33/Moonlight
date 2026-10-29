"""Refill settings: when a ``check`` waypoint sends the bot to town.

The town trip itself is part of the waypoint list (``check``, ``say``,
``key``, ``drag``; see :mod:`moonlight.cavebot.cavebot`).
"""

# doc: docs/prs/refill.md#check-waypoint

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

__all__ = ["LAST_READ", "RefillConfig", "supplies_low"]

logger = logging.getLogger(__name__)

#: Option field -> (number, picture) from the last ``check`` (None: unknown /
#: not found). The cavebot tab shows it next to each setting.
# ponytail: module global, fine while the bot runs in the GUI's process.
LAST_READ: dict[str, tuple[int | None, np.ndarray | None]] = {}


@dataclass(frozen=True)
class RefillConfig:
    #: A ``check`` waypoint jumps to its label with less free cap than this,
    #: read from the "Cap:" box on the side panel (0 = off).
    cap_below: int = 0
    #: A ``check`` also jumps with fewer health / mana potions than this
    #: on the action bar (0 = off).
    health_potions_below: int = 0
    mana_potions_below: int = 0


# doc: docs/prs/potion-count.md#last-read
def supplies_low(
    config: RefillConfig | None, grab: Callable[[], np.ndarray] | None
) -> Callable[[], bool] | None:
    """The cavebot's cap and potion refill trigger, or None when all are off.

    Each call reads every number that is on, logs one ``check:`` line per
    number ending with the verdict, and fills :data:`LAST_READ`.
    """
    if config is None or grab is None:
        return None
    from moonlight.perception.cap import CapReader
    from moonlight.perception.potions import PotionCounter

    checks = [
        ("refill.cap_below", "cap", config.cap_below),
        ("refill.health_potions_below", "HP pots", config.health_potions_below),
        ("refill.mana_potions_below", "MP pots", config.mana_potions_below),
    ]
    checks = [c for c in checks if c[2] > 0]
    if not checks:
        return None
    reader = CapReader(grab) if config.cap_below > 0 else None
    counter = PotionCounter(grab) if len(checks) > (reader is not None) else None

    def low() -> bool:
        got: dict[str, tuple[int | None, np.ndarray | None]] = {}
        if reader is not None:
            got["refill.cap_below"] = (reader.cap(), reader.crop)
        if counter is not None:
            counts = counter.counts()
            for kind in ("health", "mana"):
                got[f"refill.{kind}_potions_below"] = (counts[kind], counter.crops.get(kind))
        due = any((n := got[f][0]) is not None and n < below for f, _, below in checks)
        verdict = "refill" if due else "continue"
        for field, name, below in checks:
            LAST_READ[field] = got[field]
            n = got[field][0]
            if n is None:
                logger.info("check: %s ? | %s", name, verdict)
            else:
                sign = "<" if n < below else ">="
                logger.info("check: %s %d %s %d | %s", name, n, sign, below, verdict)
        return due

    return low
