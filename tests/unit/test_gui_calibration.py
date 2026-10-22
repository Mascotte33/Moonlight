"""Calibration tab zoom maths and file helpers (C2.F4.K2). No window opens (R8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.gui.tabs.calibration import (
    AREA_HELP,
    MIN_MARGIN,
    box_at,
    drag_rect,
    fit_scale,
    has_reference_images,
    load_old_profile,
    margin_for,
    parse_ui_scale,
    to_canvas,
    to_screenshot,
    view_rect,
)
from moonlight.setup.calibrate import REGIONS, CalibrationProfile, Region

SIZE = (2560, 1369)


def test_view_rect_none_is_whole_screenshot() -> None:
    assert view_rect(None, SIZE, 50) == (0, 0, 2560, 1369)


def test_view_rect_adds_margin_and_clamps() -> None:
    assert view_rect((100, 100, 20, 10), SIZE, 40) == (60, 60, 100, 90)
    assert view_rect((10, 5, 20, 10), SIZE, 40) == (0, 0, 70, 55)
    assert view_rect((2540, 1360, 20, 9), SIZE, 40) == (2500, 1320, 60, 49)


def test_view_rect_box_outside_stays_inside_screenshot() -> None:
    x, y, w, h = view_rect((3000, 2000, 10, 10), SIZE, 40)
    assert 0 <= x < SIZE[0] and 0 <= y < SIZE[1]
    assert w >= 1 and h >= 1 and x + w <= SIZE[0] and y + h <= SIZE[1]


def test_margin_is_half_box_with_floor() -> None:
    assert margin_for((0, 0, 400, 30)) == 200
    assert margin_for((0, 0, 10, 10)) == MIN_MARGIN


def test_screen_and_screenshot_round_trip() -> None:
    view = (60, 60, 100, 90)
    scale = fit_scale(view, (500, 800))
    assert scale == pytest.approx(5.0)
    box = (100, 100, 20, 10)
    x0, y0, x1, y1 = to_canvas(box, view, scale)
    assert (x0, y0, x1, y1) == pytest.approx((200, 200, 300, 250))
    assert to_screenshot((x0, y0), view, scale) == (100, 100)
    assert to_screenshot((x1, y1), view, scale) == (120, 110)


def test_drag_rect_normalizes_and_clamps() -> None:
    view, scale = (60, 60, 100, 90), 5.0
    # dragged up-left: same box as down-right
    assert drag_rect((300, 250), (200, 200), view, scale, SIZE) == (100, 100, 20, 10)
    # past the screenshot's top-left corner: clamped to 0
    whole = (0, 0, 2560, 1369)
    assert drag_rect((-50, -50), (100, 40), whole, 1.0, SIZE) == (0, 0, 100, 40)
    assert drag_rect((2500, 1300), (2700, 1500), whole, 1.0, SIZE) == (2500, 1300, 60, 69)


def test_drag_rect_click_is_none() -> None:
    assert drag_rect((10, 10), (11, 40), (0, 0, 100, 100), 1.0, SIZE) is None
    assert drag_rect((-90, -90), (-10, -10), (0, 0, 100, 100), 1.0, SIZE) is None


def test_box_at_picks_smallest_holding_box() -> None:
    boxes = {"minimap": (0, 0, 100, 100), "floor_indicator": (80, 10, 10, 30)}
    assert box_at((85, 20), boxes) == "floor_indicator"
    assert box_at((5, 5), boxes) == "minimap"
    assert box_at((500, 500), boxes) is None


def test_parse_ui_scale() -> None:
    assert parse_ui_scale(" 1.25 ") == 1.25
    assert parse_ui_scale("0.5") == 0.5 and parse_ui_scale("3") == 3.0
    for bad in ("", "abc", "0.4", "3.1", "nan"):
        assert parse_ui_scale(bad) is None


def test_has_reference_images(tmp_path: Path) -> None:
    assert not has_reference_images(tmp_path / "missing")
    assert not has_reference_images(tmp_path)
    (tmp_path / "stop_button.png").write_bytes(b"x")
    assert has_reference_images(tmp_path)


def test_load_old_profile(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    assert load_old_profile(path) is None
    path.write_text("not json", encoding="utf-8")
    assert load_old_profile(path) is None
    CalibrationProfile((0, 0), {"hp_bar": Region.from_tuple((1, 2, 3, 4))}, (800, 600), 1.5).save(
        path
    )
    old = load_old_profile(path)
    assert old is not None
    assert old.regions["hp_bar"].to_tuple == (1, 2, 3, 4)
    assert old.ui_scale == 1.5


def test_every_area_has_help() -> None:
    assert set(AREA_HELP) == set(REGIONS)
    assert all(t.endswith(".") for t in AREA_HELP.values())
