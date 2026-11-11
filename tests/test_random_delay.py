from __future__ import annotations

import pytest

from moonlight.core import random_delay as module
from moonlight.core.random_delay import random_delay


def test_spread_both_ways_and_only_longer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "RANDOM_DELAY_SPREAD", 0.1)
    both = [random_delay(1000) for _ in range(500)]
    longer = [random_delay(1000, only_longer=True) for _ in range(500)]
    assert 900 <= min(both) < 950 and 1050 < max(both) <= 1100
    assert 1000 <= min(longer) < 1050 and max(longer) <= 1100


# doc: docs/prs/human-timing.md#reaction
def test_reaction_is_never_quicker_and_typing_gaps_stay_in_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "REACTION_SPREAD", 0.5)
    monkeypatch.setattr(module, "TYPING_GAP_SECONDS", (0.1, 0.3))
    reactions = [module.random_reaction(0.4) for _ in range(500)]
    gaps = [module.typing_gap() for _ in range(500)]
    assert 0.4 <= min(reactions) < 0.45 and 0.55 < max(reactions) <= 0.6
    assert 0.1 <= min(gaps) < 0.15 and 0.25 < max(gaps) <= 0.3
