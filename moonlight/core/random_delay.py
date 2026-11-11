"""Random spread on the fixed waits between actions.

# doc: docs/prs/random-delays.md#spread

Pick the wait once, when its action fires, never again every tick: a
fresh pick per tick fires at the first low one, so the waits come out short.
Tests set the spreads to 0 (``tests/conftest.py``) for exact timings.
"""

from __future__ import annotations

import random

__all__ = [
    "RANDOM_DELAY_SPREAD",
    "REACTION_SPREAD",
    "TYPING_GAP_SECONDS",
    "random_delay",
    "random_reaction",
    "typing_gap",
]

#: A wait is its base +- this fraction.
RANDOM_DELAY_SPREAD = 0.10
#: A reaction to something on screen takes its base up to this fraction longer.
REACTION_SPREAD = 0.5
#: Seconds between two typed keys, lowest and highest (the wire cap is 0.1 s).
TYPING_GAP_SECONDS = (0.10, 0.30)


def random_delay(base: float, *, only_longer: bool = False) -> float:
    """*base* (seconds or ms) moved by up to ``RANDOM_DELAY_SPREAD`` either way.

    *only_longer*: never below *base*, for cooldowns the game itself enforces.
    """
    lowest = 1.0 if only_longer else 1.0 - RANDOM_DELAY_SPREAD
    return base * random.uniform(lowest, 1.0 + RANDOM_DELAY_SPREAD)


# doc: docs/prs/human-timing.md#reaction
def random_reaction(base: float) -> float:
    """*base* up to ``REACTION_SPREAD`` longer: never quicker than the base."""
    return base * random.uniform(1.0, 1.0 + REACTION_SPREAD)


# doc: docs/prs/human-timing.md#typing
def typing_gap() -> float:
    """Seconds to wait after a typed key before the next one."""
    return random.uniform(*TYPING_GAP_SECONDS)
