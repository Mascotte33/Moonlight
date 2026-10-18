"""Offline tests for the server-log OCR reader (``_ocr`` mocked)."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from moonlight.perception.logocr import EventKind, LogEvent, LogFrame, LogOCR, LogOCRConfig

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
    with (
        patch.object(LogOCR, "_ocr", return_value=text),
        patch.object(LogOCR, "_ocr_confidence", return_value=conf),
    ):
        return reader.read(frame, config or cfg())


def test_defaults_fail_closed() -> None:
    c = LogOCRConfig(log_region=(0, 0, 10, 10))
    assert c.enabled is False and c.min_confidence == 60


def test_disabled_returns_empty() -> None:
    r = read("You got a sword", cfg(enabled=False))
    assert r == LogFrame(lines=[], events=[], ok=False, confidence=0.0)


def test_ocr_raises_fails_closed() -> None:
    with patch.object(LogOCR, "_ocr", side_effect=RuntimeError("no tesseract")):
        r = LogOCR().read(FRAME, cfg())
    assert not r.ok and r.lines == [] and r.events == []


def test_confidence_raises_fails_closed() -> None:
    with (
        patch.object(LogOCR, "_ocr", return_value="x"),
        patch.object(LogOCR, "_ocr_confidence", side_effect=ValueError),
    ):
        assert not LogOCR().read(FRAME, cfg()).ok


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


def test_ocr_receives_scaled_gray_crop() -> None:
    seen: list[tuple[int, ...]] = []

    def fake(self: LogOCR, image: np.ndarray) -> str:
        seen.append(image.shape)
        return ""

    with (
        patch.object(LogOCR, "_ocr", fake),
        patch.object(LogOCR, "_ocr_confidence", return_value=90.0),
    ):
        LogOCR().read(FRAME, cfg(log_region=(0, 0, 40, 20), scale=2))
    assert seen == [(40, 80)]


def test_grayscale_frame_supported() -> None:
    assert read("x", frame=np.zeros((100, 100), dtype=np.uint8)).ok
