"""Random spread on the fixed waits between actions.

# doc: docs/prs/random-delays.md#spread

Pick the wait once, when its action fires, never again every tick: a
fresh pick per tick fires at the first low one, so the waits come out short.
"""

from __future__ import annotations

import random

__all__ = ["RANDOM_DELAY_SPREAD", "random_delay"]

#: A wait is its base +- this fraction (tests set it to 0 for exact timings).
RANDOM_DELAY_SPREAD = 0.10


def random_delay(base: float, *, only_longer: bool = False) -> float:
    """*base* (seconds or ms) moved by up to ``RANDOM_DELAY_SPREAD`` either way.

    *only_longer*: never below *base*, for cooldowns the game itself enforces.
    """
    lowest = 1.0 if only_longer else 1.0 - RANDOM_DELAY_SPREAD
    return base * random.uniform(lowest, 1.0 + RANDOM_DELAY_SPREAD)
