"""Unit tests for clicker.py (T17 closed-loop clicking).

Focus (all offline, deterministic):
    - Exact moves (move_scale=1.0): converges in one step, returns True,
      sends exactly ONE click wire line ('C L'), lands within tolerance;
    - Imprecise moves (move_scale=0.9 / 1.1, simulating a lossy USB HID):
      the correction loop issues extra M commands and still converges to
      within tolerance;
    - Game grid snapping (move_snap>1): still converges (targets on the
      grid land exactly, off-grid targets land within tolerance);
    - Cursor not found (frame without the template): click_at returns
      False and no 'C ' wire line is sent (fail-closed);
    - Non-convergence (move_scale=0.01, small max_iters): returns False,
      no click sent;
    - Wire moves: a far target is one M command (the firmware splits it);
    - Validation: bad target shapes, tolerance<0, max_iters<1 -> ValueError;
    - ClickSide.RIGHT sends 'C R'.

Harness: FakeInput (moonlight.hardware.input) tracks a virtual cursor position; a
frame_provider renders the cursor template at fake.cursor_position onto a
blank RGB frame, so CursorReader reads the fake's live position — a true
closed loop over synthetic pixels.

Run:
    python -m pytest tests/unit/test_clicker.py
"""

from __future__ import annotations

import numpy as np
import pytest

from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import MAX_MOUSE_DELTA, ClickSide, FakeInput
from moonlight.perception import CursorReader

# ---------------------------------------------------------------------------
# Synthetic fixtures / harness
# ---------------------------------------------------------------------------

FRAME_W, FRAME_H = 320, 200
REGION = (0, 0, FRAME_W, FRAME_H)
BG = 40  # uniform gray background (no structure -> no false matches)


def _make_cursor_template() -> np.ndarray:
    """Distinctive 11x11 pattern (odd size -> clean center)."""
    tpl = np.zeros((11, 11), dtype=np.uint8)
    tpl[:, 5] = 255
    tpl[5, :] = 255
    tpl[1, 1] = 250
    tpl[9, 9] = 200
    tpl[3, 7] = 170
    return tpl


TEMPLATE = _make_cursor_template()


def make_frame(
    center: tuple[int, int] | None = None,
    template: np.ndarray = TEMPLATE,
) -> np.ndarray:
    """Blank RGB frame with *template* rendered centered at *center*."""
    frame = np.full((FRAME_H, FRAME_W, 3), BG, dtype=np.uint8)
    if center is not None:
        cx, cy = center
        x0 = cx - template.shape[1] // 2
        y0 = cy - template.shape[0] // 2
        frame[y0 : y0 + template.shape[0], x0 : x0 + template.shape[1]] = (
            template[:, :, np.newaxis]
        )
    return frame


def build_harness(
    cursor: tuple[int, int],
    move_scale: float = 1.0,
    move_snap: int = 1,
    include_cursor: bool = True,
) -> tuple[FakeInput, ClickController]:
    """A FakeInput + frame_provider + ClickController closed loop.

    The frame provider renders the cursor template at the fake's *current*
    virtual position on every call, so each read() sees the real result of
    the previous move.
    """
    fake = FakeInput(cursor=cursor, move_scale=move_scale, move_snap=move_snap)

    def frame_provider() -> np.ndarray:
        center = fake.cursor_position if include_cursor else None
        return make_frame(center=center)

    reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
    return fake, ClickController(fake, reader, frame_provider)


def m_lines(sent: list[str]) -> list[tuple[int, int]]:
    """Parse the (dx, dy) of every M command in *sent* wire lines."""
    out: list[tuple[int, int]] = []
    for line in sent:
        parts = line.split()
        if parts and parts[0] == "M" and len(parts) == 3:
            out.append((int(parts[1]), int(parts[2])))
    return out


def click_lines(sent: list[str], prefix: str = "C ") -> int:
    return sum(1 for line in sent if line.startswith(prefix))


# ---------------------------------------------------------------------------
# Convergence behavior
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_exact_move_converges_and_clicks_once(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80), move_scale=1.0)
        ok = clicker.click_at((150, 90))
        assert ok is True
        # Exactly one move, then exactly one left click.
        assert fake.sent_lines == ["M 70 10", "C L"]
        assert fake.cursor_position == (150, 90)

    def test_imprecise_move_09_converges_with_corrections(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80), move_scale=0.9)
        ok = clicker.click_at((150, 90))
        assert ok is True
        moves = m_lines(fake.sent_lines)
        # The imprecise backend needs at least one correction beyond the
        # initial move.
        assert len(moves) > 1
        assert click_lines(fake.sent_lines) == 1
        assert fake.sent_lines[-1] == "C L"
        assert abs(fake.cursor_position[0] - 150) <= 2
        assert abs(fake.cursor_position[1] - 90) <= 2

    def test_imprecise_move_11_converges_with_corrections(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80), move_scale=1.1)
        ok = clicker.click_at((150, 90))
        assert ok is True
        assert len(m_lines(fake.sent_lines)) > 1
        assert click_lines(fake.sent_lines) == 1
        assert fake.sent_lines[-1] == "C L"
        assert abs(fake.cursor_position[0] - 150) <= 2
        assert abs(fake.cursor_position[1] - 90) <= 2

    def test_correction_moves_bounded_by_max_iters(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80), move_scale=0.9)
        ok = clicker.click_at((250, 170), max_iters=3)
        assert ok is True
        # Initial move + at most max_iters correction moves.
        assert len(m_lines(fake.sent_lines)) <= 1 + 3
        assert click_lines(fake.sent_lines) == 1

    def test_already_at_target_clicks_without_moves(self) -> None:
        fake, clicker = build_harness(cursor=(120, 95), move_scale=1.0)
        ok = clicker.click_at((120, 95))
        assert ok is True
        assert fake.sent_lines == ["C L"]

    def test_grid_snap_on_grid_target_lands_exactly(self) -> None:
        # move_snap=3: a target on the game grid is reachable exactly.
        fake, clicker = build_harness(cursor=(80, 80), move_scale=1.0, move_snap=3)
        ok = clicker.click_at((150, 90))
        assert ok is True
        assert fake.cursor_position == (150, 90)
        assert click_lines(fake.sent_lines) == 1

    def test_grid_snap_off_grid_target_within_tolerance(self) -> None:
        # Off-grid target: snap can leave at most ~snap//2 px of error,
        # which the default tolerance=2 covers for snap=3.
        fake, clicker = build_harness(cursor=(80, 80), move_scale=1.0, move_snap=3)
        ok = clicker.click_at((151, 92))
        assert ok is True
        assert abs(fake.cursor_position[0] - 151) <= 2
        assert abs(fake.cursor_position[1] - 92) <= 2

    def test_cursor_not_found_returns_false_no_click(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80), include_cursor=False)
        ok = clicker.click_at((150, 90))
        assert ok is False
        assert click_lines(fake.sent_lines) == 0
        # Fail-closed before any move is even issued.
        assert fake.sent_lines == []

    def test_non_convergence_returns_false_no_click(self) -> None:
        # A backend that barely moves falls short; with max_iters=1 the
        # budget runs out before the learned gain catches up.
        fake, clicker = build_harness(cursor=(10, 10), move_scale=0.01)
        ok = clicker.click_at((210, 110), max_iters=1)
        assert ok is False
        assert click_lines(fake.sent_lines) == 0
        # It did try: several moves were issued.
        assert len(m_lines(fake.sent_lines)) >= 2


class AccelInput(FakeInput):
    """Windows pointer acceleration (MouseSpeed=1): moves over 6 counts double."""

    def mouse_move(self, dx: int, dy: int) -> None:
        super().mouse_move(dx, dy)
        x, y = self.cursor_position
        self.cursor_position = (x + (dx if abs(dx) > 6 else 0), y + (dy if abs(dy) > 6 else 0))


def test_pointer_acceleration_converges_instead_of_oscillating() -> None:
    fake = AccelInput(cursor=(80, 80))
    reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
    clicker = ClickController(fake, reader, lambda: make_frame(center=fake.cursor_position))
    assert clicker.click_at((150, 90)) is True
    assert abs(fake.cursor_position[0] - 150) <= 2
    assert abs(fake.cursor_position[1] - 90) <= 2


# ---------------------------------------------------------------------------
# Chunking / wire format
# ---------------------------------------------------------------------------


class TestWireMoves:
    def test_far_target_is_one_move(self) -> None:
        # dx=270, dy=180: within MAX_MOUSE_DELTA, so one exact M command.
        fake, clicker = build_harness(cursor=(10, 10), move_scale=1.0)
        assert clicker.click_at((280, 190)) is True
        assert m_lines(fake.sent_lines) == [(270, 180)]
        assert click_lines(fake.sent_lines) == 1
        assert fake.cursor_position == (280, 190)


# ---------------------------------------------------------------------------
# Click side
# ---------------------------------------------------------------------------


class TestClickSide:
    def test_default_side_is_left(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80))
        assert clicker.click_at((150, 90)) is True
        assert "C L" in fake.sent_lines
        assert "C R" not in fake.sent_lines

    def test_right_side_sends_right_click(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80))
        ok = clicker.click_at((150, 90), side=ClickSide.RIGHT)
        assert ok is True
        assert click_lines(fake.sent_lines) == 1
        assert "C R" in fake.sent_lines
        assert "C L" not in fake.sent_lines


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_bad_target_shapes_raise(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80))
        with pytest.raises(ValueError):
            clicker.click_at((100,))  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            clicker.click_at((1, 2, 3))  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            clicker.click_at("xy")  # type: ignore[arg-type]

    def test_negative_tolerance_raises(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80))
        with pytest.raises(ValueError):
            clicker.click_at((150, 90), tolerance=-1)

    def test_max_iters_zero_raises(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80))
        with pytest.raises(ValueError):
            clicker.click_at((150, 90), max_iters=0)

    def test_validation_errors_do_not_touch_wire(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80))
        with pytest.raises(ValueError):
            clicker.click_at((100,))  # type: ignore[arg-type]
        assert fake.sent_lines == []


# ---------------------------------------------------------------------------
# Per-tick state machine (P5 / F-14)
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_step_does_at_most_one_capture_and_one_wire_command(self) -> None:
        fake = FakeInput(cursor=(10, 10))
        captures: list[int] = []

        def frame_provider() -> np.ndarray:
            captures.append(1)
            return make_frame(center=fake.cursor_position)

        reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
        clicker = ClickController(fake, reader, frame_provider, sleep_fn=lambda s: None)
        clicker.request((280, 190))
        results = []
        while clicker.busy:
            before_caps, before_lines = len(captures), len(fake.sent_lines)
            results.append(clicker.step())
            assert len(captures) - before_caps <= 1
            assert len(fake.sent_lines) - before_lines <= 1
        assert results[-1] is True and results[:-1] == [None] * (len(results) - 1)
        assert fake.sent_lines[-1] == "C L"

    def test_reads_after_first_use_small_roi_via_roi_provider(self) -> None:
        fake = FakeInput(cursor=(80, 80))
        regions: list[tuple[int, int, int, int]] = []

        def roi(region: tuple[int, int, int, int]) -> np.ndarray:
            regions.append(region)
            return make_frame(center=fake.cursor_position)

        reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
        clicker = ClickController(
            fake, reader, lambda: pytest.fail("full frame used"), roi_provider=roi,
            sleep_fn=lambda s: None,
        )
        assert clicker.click_at((150, 90)) is True
        assert regions[0] == REGION
        assert regions[1] == (150 - 48, 90 - 48, 96, 96)
        # The next click's first read starts where the cursor was last seen.
        regions.clear()
        assert clicker.click_at((60, 60)) is True
        assert regions[0] == (150 - 48, 90 - 48, 96, 96)
        # Cursor moved by someone else: the ROI misses, then the full region.
        fake.cursor_position = (300, 20)
        regions.clear()
        assert clicker.click_at((250, 30)) is True
        assert regions[:2] == [(60 - 48, 60 - 48, 96, 96), REGION]

    def test_settle_delay_before_read_after_move(self) -> None:
        now = [0.0]
        sleeps: list[float] = []
        fake = FakeInput(cursor=(80, 80))
        reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
        clicker = ClickController(
            fake, reader, lambda: make_frame(center=fake.cursor_position),
            clock=lambda: now[0], sleep_fn=sleeps.append,
        )
        clicker.request((150, 90))
        assert clicker.step() is None  # move sent at t=0
        assert sleeps == []
        assert clicker.step() is True  # read right after the move: waits 20 ms
        assert sleeps == [pytest.approx(0.02)]

    def test_failure_drops_queued_follow_up_click(self) -> None:
        fake, clicker = build_harness(cursor=(80, 80), include_cursor=False)
        clicker.request((150, 90))
        clicker.request((150, 90))
        assert clicker.step() is False
        assert not clicker.busy and fake.sent_lines == []

    def test_open_loop_homes_then_dead_reckons_scaled(self) -> None:
        from moonlight.hardware.clicker import ClickerConfig

        fake = FakeInput(cursor=(0, 0))
        reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
        cfg = ClickerConfig(open_loop=True, mouse_scale=2.0, home_moves=3)
        clicker = ClickController(
            fake, reader, lambda: pytest.fail("open loop must not capture"),
            config=cfg, window_origin=lambda: (100, 50), sleep_fn=lambda s: None,
        )
        assert clicker.click_at((60, 30)) is True
        moves = m_lines(fake.sent_lines)
        assert moves[:3] == [(-MAX_MOUSE_DELTA, -MAX_MOUSE_DELTA)] * 3
        # From the screen corner (-100, -50) to (60, 30) is (160, 80) px at
        # 2 px per count: 80 + 40 counts in total.
        assert sum(m[0] for m in moves[3:]) == 80
        assert sum(m[1] for m in moves[3:]) == 40
        assert fake.sent_lines[-1] == "C L"


# ---------------------------------------------------------------------------
# Absolute mode (firmware ``A`` command)
# ---------------------------------------------------------------------------


def _absolute(**kwargs: object) -> tuple[FakeInput, ClickController]:
    fake = FakeInput()
    reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
    clicker = ClickController(
        fake,
        reader,
        lambda: make_frame(center=None),
        screen=(0, 0, 1000, 500),
        window_origin=lambda: (100, 50),
        sleep_fn=lambda _s: None,
        **kwargs,  # type: ignore[arg-type]
    )
    return fake, clicker


def test_absolute_jumps_then_clicks_without_reading_the_cursor() -> None:
    fake, clicker = _absolute()
    clicker.request((400, 200))
    assert clicker.next_read() is None
    assert clicker.step() is None
    assert clicker.step() is True
    # screen point (500, 250) on a 1000x500 screen -> the middle of 0..32767
    assert fake.sent_lines == ["A 16400 16417", "C L"]
    assert not clicker.busy


def test_absolute_parks_off_the_minimap_after_a_minimap_click() -> None:
    fake, clicker = _absolute(park_region=(800, 0, 100, 100))
    clicker.request((850, 50))
    clicker.step()
    clicker.step()
    assert clicker.busy  # the park is still pending
    clicker.step()
    assert fake.sent_lines[-1] == "A 28983 6586"  # screen (884, 100)
    assert not clicker.busy

    fake.sent_lines.clear()
    clicker.request((10, 10))  # outside the minimap: no park
    clicker.step()
    clicker.step()
    assert fake.sent_lines == ["A 3621 3965", "C L"] and not clicker.busy


def test_absolute_retries_a_rate_limited_jump() -> None:
    from moonlight.bot.safety import RateLimitedInput, RateLimiter

    now = [0.0]
    limiter = RateLimiter(rate=10, capacity=2.0, clock=lambda: now[0])
    limited = RateLimitedInput(FakeInput(), limiter)
    reader = CursorReader(cursor_region=REGION, cursor_templates=[TEMPLATE])
    clicker = ClickController(
        limited, reader, lambda: make_frame(center=None), screen=(0, 0, 1000, 500)
    )
    limited.limiter.try_acquire(1.0)  # bucket below the reserve
    clicker.request((0, 0))
    assert clicker.step() is None and limited.dropped == 1
    now[0] += 0.2
    clicker.step()
    now[0] += 0.2
    assert clicker.step() is True
    assert isinstance(limited.backend, FakeInput)
    assert limited.backend.sent_lines == ["A 16 33", "C L"]
