"""Offline tests for the server-log OCR reader (``_ocr`` mocked)."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from moonlight.perception.logocr import (
    EventKind,
    LogEvent,
    LogFrame,
    LogOCR,
    LogOCRConfig,
    LogOCRWorker,
    health,
)

FRAME = np.zeros((200, 300, 3), dtype=np.uint8)


def cfg(**kw: object) -> LogOCRConfig:
    base: dict[str, object] = {"log_region": (10, 20, 100, 50), "enabled": True}
    base.update(kw)
    return LogOCRConfig(**base)  # type: ignore[arg-type]


def read(
    text: str,
    config: LogOCRConfig | None = None,
    conf: float = 90.0,
    reader: LogOCR | None = None,
    frame: np.ndarray = FRAME,
) -> LogFrame:
    reader = reader or LogOCR()
    with patch.object(LogOCR, "_ocr_lines", return_value=(text.splitlines(), conf)):
        return reader.read(frame, config or cfg())


def test_defaults_fail_closed() -> None:
    c = LogOCRConfig(log_region=(0, 0, 10, 10))
    assert c.enabled is False and c.min_confidence == 60


def test_disabled_returns_empty() -> None:
    r = read("You got a sword", cfg(enabled=False))
    assert r == LogFrame(lines=[], events=[], ok=False, confidence=0.0)


def test_ocr_raises_fails_closed_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    """F-21: the first failure is logged with its exception, then silence."""
    reader = LogOCR()
    with patch.object(LogOCR, "_ocr_lines", side_effect=RuntimeError("no tesseract")):
        r = reader.read(FRAME, cfg())
        reader.read(FRAME, cfg())
    assert not r.ok and r.lines == [] and r.events == []
    assert isinstance(reader.last_error, RuntimeError)
    warnings = [rec for rec in caplog.records if "log OCR failed" in rec.getMessage()]
    assert len(warnings) == 1 and "no tesseract" in warnings[0].getMessage()
    read("x", reader=reader)
    assert reader.last_error is None


def test_health_reports_tesseract_state() -> None:
    ok, detail = health()
    assert isinstance(ok, bool) and "tesseract" in detail


def test_real_ocr_never_raises() -> None:
    # with or without the tesseract binary installed
    assert isinstance(LogOCR().read(FRAME, cfg()), LogFrame)


def test_low_confidence_filtered() -> None:
    r = read("You got a sword", conf=59.0)
    assert not r.ok and r.events == []


def test_confidence_at_threshold_ok() -> None:
    r = read("You got a sword", conf=60.0)
    assert r.ok and r.confidence == pytest.approx(0.6)


def test_custom_min_confidence() -> None:
    assert not read("a", cfg(min_confidence=95), conf=90.0).ok


def test_line_splitting_drops_blanks() -> None:
    assert read("one\n\n  two  \n   \nthree\n").lines == ["one", "two", "three"]


def test_empty_text_ok_no_events() -> None:
    r = read("")
    assert r.ok and r.lines == [] and r.events == []


def test_loot_classified() -> None:
    assert read("Loot of a rat: cheese").events[0].kind is EventKind.LOOT
    assert read("You found a gold coin").events[0].kind is EventKind.LOOT


def test_damage_classified() -> None:
    assert read("A rat hit you for 5 hitpoints").events[0].kind is EventKind.DAMAGE


def test_kill_classified() -> None:
    assert read("A rat died").events[0].kind is EventKind.KILL


def test_kill_beats_loot() -> None:
    assert read("You killed it and got loot").events[0].kind is EventKind.KILL


def test_other_classified() -> None:
    assert read("Welcome to the game").events[0].kind is EventKind.OTHER


def test_case_insensitive() -> None:
    assert read("A RAT DIED").events[0].kind is EventKind.KILL


def test_custom_keywords() -> None:
    c = cfg(loot_keywords=("pickup",))
    assert read("pickup sword", c).events[0].kind is EventKind.LOOT
    assert read("You got it", c).events[0].kind is EventKind.OTHER


def test_event_shape() -> None:
    ev = read("A rat died", conf=80.0).events[0]
    assert ev == LogEvent(EventKind.KILL, "A rat died", 0.8)


def test_dedupe_same_line_once() -> None:
    reader = LogOCR()
    assert len(read("A rat died", reader=reader).events) == 1
    assert read("A rat died", reader=reader).events == []


def test_dedupe_only_new_lines_emitted() -> None:
    reader = LogOCR()
    read("A rat died", reader=reader)
    r = read("A rat died\nYou found a coin", reader=reader)
    assert [e.text for e in r.events] == ["You found a coin"]
    assert len(r.lines) == 2


def test_dedupe_reemits_after_scrolled_off() -> None:
    reader = LogOCR()
    read("A rat died", reader=reader)
    read("something else", reader=reader)
    assert len(read("A rat died", reader=reader).events) == 1


def test_region_crop_math() -> None:
    frame = np.arange(200 * 300 * 3, dtype=np.uint32).astype(np.uint8).reshape(200, 300, 3)
    crop = LogOCR._crop(frame, cfg(log_region=(10, 20, 100, 50)))
    assert crop is not None and crop.shape == (50, 100, 3)
    assert np.array_equal(crop, frame[20:70, 10:110])


def test_region_clipped_to_frame() -> None:
    crop = LogOCR._crop(FRAME, cfg(log_region=(250, 180, 100, 100)))
    assert crop is not None and crop.shape == (20, 50, 3)


def test_region_outside_frame_fails_closed() -> None:
    assert not read("x", cfg(log_region=(500, 500, 50, 50))).ok


def test_region_too_small_fails_closed() -> None:
    assert not read("x", cfg(log_region=(0, 0, 4, 4))).ok


def test_ocr_receives_scaled_binary_crop() -> None:
    seen: list[np.ndarray] = []

    def fake(self: LogOCR, image: np.ndarray) -> tuple[list[str], float]:
        seen.append(image)
        return [], 90.0

    frame = np.random.default_rng(0).integers(0, 255, (200, 300, 3), dtype=np.uint8)
    with patch.object(LogOCR, "_ocr_lines", fake):
        LogOCR().read(frame, cfg(log_region=(0, 0, 40, 20), scale=2))
    assert len(seen) == 1 and seen[0].shape == (40, 80)


def test_line_height_crops_only_the_newest_lines() -> None:
    """P4: with line_height calibrated, only the bottom max_lines rows are read."""
    crop = LogOCR._crop(FRAME, cfg(log_region=(10, 20, 100, 50), line_height=12, max_lines=3))
    assert crop is not None and crop.shape == (36, 100, 3)


def test_only_last_max_lines_kept() -> None:
    assert read("a\nb\nc\nd", cfg(max_lines=2)).lines == ["c", "d"]


def test_dedupe_by_position_keeps_repeated_identical_lines() -> None:
    """F-21: two identical new lines are two events; a scroll re-emits nothing."""
    reader = LogOCR()
    read("A rat died", reader=reader)
    r = read("A rat died\nYou loot nothing\nYou loot nothing", reader=reader)
    assert [e.text for e in r.events] == ["You loot nothing", "You loot nothing"]
    r = read("You loot nothing\nYou loot nothing\nA rat died", reader=reader)
    assert [e.text for e in r.events] == ["A rat died"]


def test_worker_reads_off_tick_and_drains_events() -> None:
    """P4: the worker polls frames and queues events for the tick to drain."""
    reader = LogOCR()
    worker = LogOCRWorker(reader, cfg(), lambda: FRAME)
    with patch.object(LogOCR, "_ocr_lines", return_value=(["A rat died"], 90.0)):
        worker.poll()
        worker.poll()
    assert [e.kind for e in worker.drain()] == [EventKind.KILL]
    assert worker.drain() == [] and worker.latest.ok
    with pytest.raises(ValueError):
        LogOCRWorker(reader, cfg(), lambda: FRAME, interval=0.5)


def test_worker_thread_starts_and_stops() -> None:
    worker = LogOCRWorker(LogOCR(), cfg(enabled=False), lambda: FRAME)
    worker.start()
    worker.stop()
    assert not worker._thread.is_alive()


def test_grayscale_frame_supported() -> None:
    assert read("x", frame=np.zeros((100, 100), dtype=np.uint8)).ok
