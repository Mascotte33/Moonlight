"""Status tab position age text (T9.F3.K1)."""

from __future__ import annotations

from moonlight.gui.tabs.status import age_text


def test_age_text() -> None:
    assert age_text(97.4, 100.0) == "updated 2 s ago"
    assert age_text(0.0, 100.0) == "never"
