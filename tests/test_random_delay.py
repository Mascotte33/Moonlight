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
