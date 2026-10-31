"""Status tab position age text (T9.F3.K1) and Diagnostics recent errors (T10.F4.K1)."""

from __future__ import annotations

import pytest

from moonlight.gui.dashboard_tab import recent_errors
from moonlight.gui.state_bridge import HardwareController, LogFeed
from moonlight.gui.status_tab import age_text


def test_age_text() -> None:
    assert age_text(97.4, 100.0) == "updated 2 s ago"
    assert age_text(0.0, 100.0) == "never"


def test_recent_errors_newest_first_without_info() -> None:
    log = LogFeed()
    assert recent_errors(log) == "No errors"
    log.warn("first")
    log.info("chatter")
    log.error("second")
    lines = recent_errors(log).splitlines()
    assert [ln.split("] ", 2)[-1] for ln in lines] == ["second", "first"]


def test_recent_errors_keeps_last_20() -> None:
    log = LogFeed()
    for i in range(25):
        log.error(f"e{i}")
    lines = recent_errors(log).splitlines()
    assert len(lines) == 20
    assert lines[0].endswith("e24")


def test_failed_connect_is_logged() -> None:
    log = LogFeed()

    def factory(port: str | None, baudrate: int) -> object:
        raise OSError("no port")

    hw = HardwareController(input_factory=factory, log=log)  # type: ignore[arg-type]
    with pytest.raises(OSError):
        hw.connect(port="COM9")
    assert "arduino connect failed" in recent_errors(log)
