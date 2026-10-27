"""Unit tests for moonlight.core.safety (RateLimiter, F12Monitor).

F12 uses an injectable key-state probe (tiny real sleeps for the thread).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import pytest

from moonlight.core.safety import VK_F12, F12Monitor, RateLimitedInput, RateLimiter
from moonlight.hardware.input import ClickSide, FakeInput


class FakeClock:
    """Deterministic monotonic clock with a manual sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt

    def sleep(self, dt: float) -> None:
        self.sleeps.append(dt)
        self.now += max(0.0, dt)


class KeySim:
    """Controllable fake keyboard for F12Monitor tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._down: set[int] = set()
        self.samples = 0

    def down(self, vk: int) -> None:
        with self._lock:
            self._down.add(vk)

    def up(self, vk: int) -> None:
        with self._lock:
            self._down.discard(vk)

    def __call__(self, vk: int) -> bool:
        with self._lock:
            self.samples += 1
            return vk in self._down


def wait_until(pred: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Poll *pred* until true or the timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.002)
    return pred()


class TestRateLimiter:
    def test_initial_burst_limited_to_capacity(self) -> None:
        rl = RateLimiter(rate=10.0, capacity=3.0)
        assert all(rl.try_acquire() for _ in range(3))
        assert not rl.try_acquire()

    def test_refills_at_rate(self) -> None:
        c = FakeClock()
        rl = RateLimiter(rate=10.0, capacity=1.0, clock=c)
        assert rl.try_acquire()
        assert not rl.try_acquire()
        c.advance(0.1)  # one token at 10/s
        assert rl.try_acquire()
        assert not rl.try_acquire()

    def test_tokens_capped_at_capacity(self) -> None:
        c = FakeClock()
        rl = RateLimiter(rate=10.0, capacity=2.0, clock=c)
        c.advance(10.0)  # would add 100 tokens
        assert rl.try_acquire()
        assert rl.try_acquire()
        assert not rl.try_acquire()

    def test_invalid_rate(self) -> None:
        with pytest.raises(ValueError):
            RateLimiter(rate=0.0)
        with pytest.raises(ValueError):
            RateLimiter(rate=-1.0)


class TestF12Monitor:
    def test_rising_edge_triggers_on_press(self) -> None:
        presses: list[int] = []
        key = KeySim()
        mon = F12Monitor(on_press=lambda: presses.append(1), poll_interval=0.002, key_state=key)
        mon.start()
        try:
            # Wait until the monitor has taken at least one sample (with the
            # key up), so prev_down is reliably initialized to False.
            assert wait_until(lambda: key.samples >= 1)
            key.down(VK_F12)
            assert wait_until(lambda: len(presses) == 1)
            # A held key must not retrigger.
            time.sleep(0.05)
            assert len(presses) == 1
            key.up(VK_F12)
            time.sleep(0.05)
            key.down(VK_F12)
            assert wait_until(lambda: len(presses) == 2)
        finally:
            mon.stop()
        assert not mon.running

    def test_no_press_without_key(self) -> None:
        presses: list[int] = []
        key = KeySim()
        mon = F12Monitor(on_press=lambda: presses.append(1), poll_interval=0.002, key_state=key)
        mon.start()
        try:
            time.sleep(0.05)
            assert presses == []
        finally:
            mon.stop()

    def test_invalid_poll_interval(self) -> None:
        with pytest.raises(ValueError):
            F12Monitor(on_press=lambda: None, poll_interval=0.0)


def test_keys_sent_and_dropped_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    limited = RateLimitedInput(FakeInput(), RateLimiter(rate=1.0, capacity=2.0, clock=lambda: 0.0))
    with caplog.at_level(logging.DEBUG, logger="moonlight.core.safety"):
        limited.press_combo("up")
        limited.press_combo("r")
    assert [r.getMessage() for r in caplog.records] == [
        "key up",
        "wire rate limit: dropped press_combo r",
    ]


def test_mouse_release_is_never_dropped() -> None:
    fake = FakeInput()
    limited = RateLimitedInput(fake, RateLimiter(rate=1.0, capacity=2.0, clock=lambda: 0.0))
    limited.mouse_down(ClickSide.LEFT)
    limited.mouse_down(ClickSide.LEFT)
    limited.mouse_up(ClickSide.LEFT)
    assert fake.sent_lines == ["H L", "R L"]
    assert limited.dropped == 1
