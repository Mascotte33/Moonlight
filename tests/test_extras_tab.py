"""The Extras tab follows a script's ring switch (owner 2026-11-28)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("customtkinter")

from moonlight.core.state import WorldState  # noqa: E402
from moonlight.gui.extras_tab import ExtrasTab  # noqa: E402


def test_a_script_ring_switch_shows_on_the_checkbox() -> None:
    extras = SimpleNamespace(config=SimpleNamespace(food=True, ring=False))
    box: dict[str, Any] = {"extras.food": True, "extras.ring": False}
    submits: list[bool] = []
    tab: Any = ExtrasTab.__new__(ExtrasTab)
    tab.ctx = SimpleNamespace(live_module=lambda name: extras)
    tab._readers = {k: (lambda k=k: box[k]) for k in box}
    tab._writers = {k: (lambda v, k=k: box.__setitem__(k, v)) for k in box}
    tab.submit = lambda: submits.append(True)
    tab.on_poll(WorldState())
    extras.config.ring = True  # ring_on ran
    tab.on_poll(WorldState())
    assert box["extras.ring"] is True and submits == [True]
    box["extras.ring"] = False  # the owner turns it off: the live switch follows on submit
    extras.config.ring = False
    tab.on_poll(WorldState())
    assert box["extras.ring"] is False and len(submits) == 1
