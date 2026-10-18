"""HIL scenario 1 -- click latency (offline: FakeInput / mock serial)."""

import time

import pytest
from mock_serial import MockBus

from moonlight.input import FakeInput

#: Generous offline ceiling; the in-memory path is microseconds, so this only
#: trips on a real regression (blocking sleeps, accidental I/O).
MAX_PING_MS = 50.0


def _ms(fn: object) -> float:
    t0 = time.perf_counter()
    fn()  # type: ignore[operator]
    return (time.perf_counter() - t0) * 1000.0


def test_fake_ping_roundtrip_under_threshold() -> None:
    fake = FakeInput()
    assert _ms(fake.ping) < MAX_PING_MS
    assert fake.sent_lines == ["P"]
    assert fake.last_reply == "PONG"


def test_serial_ping_roundtrip_under_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = MockBus()
    bus.install(monkeypatch)
    ard = bus.connect()
    try:
        samples = [_ms(ard.ping) for _ in range(20)]
        assert max(samples) < MAX_PING_MS
        assert bus.current.lines_written.count("P") >= 20
    finally:
        ard.close()
