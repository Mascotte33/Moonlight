"""HIL scenario 3 -- serial loss (safety-critical, offline mock serial).

A dead link cannot carry ``X``: the host's job is to detect the loss at the
first failed write, go fail-closed (refuse every command, send nothing
more) and release everything (``X``) as soon as the device is back. The
firmware watchdog covers the device side while the link is down.
"""

import time
from collections.abc import Callable

import pytest
from mock_serial import MockBus

from moonlight.hardware.input import ArduinoInputError, ClickSide, DeviceLostError


def test_release_all_sends_x_while_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = MockBus()
    bus.install(monkeypatch)
    ard = bus.connect()
    try:
        ard.hold_combo("shift")
        ard.release_all()
        assert bus.current.lines_written[-2:] == ["D shift", "X"]
    finally:
        ard.close()


def test_drop_mid_session_goes_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = MockBus()
    bus.install(monkeypatch)
    ard = bus.connect()
    try:
        ard.hold_combo("shift")
        dead = bus.current
        bus.plugged = False  # no recovery while we assert
        dead.drop()
        with pytest.raises(ArduinoInputError):
            ard.press_combo("F1")  # first failed write detects the loss
        assert not ard.is_alive
        sent = list(dead.lines_written)
        refused: list[Callable[[], None]] = [
            lambda: ard.click(ClickSide.LEFT),
            ard.release_all,
        ]
        for call in refused:
            with pytest.raises(DeviceLostError):
                call()
        assert not ard.ping()
        assert dead.lines_written == sent  # nothing sent after the loss
        assert dead.closed
    finally:
        ard.close()  # best-effort and idempotent on a dead link


def test_x_is_issued_after_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = MockBus()
    bus.install(monkeypatch)
    ard = bus.connect(heartbeat_interval=0.02)
    try:
        ard.hold_combo("shift")
        bus.plugged = False
        bus.current.drop()
        with pytest.raises(ArduinoInputError):
            ard.press_combo("F1")
        bus.plugged = True  # re-plug: the heartbeat thread reopens the device
        deadline = time.monotonic() + 3.0
        while not ard.is_alive and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ard.is_alive
        ard.release_all()
        assert bus.current is not bus.opened[0]
        assert bus.current.lines_written[-1] == "X"
    finally:
        ard.close()
