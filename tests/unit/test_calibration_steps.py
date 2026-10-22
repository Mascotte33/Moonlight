"""Tests for moonlight.setup.calibration_steps."""

from __future__ import annotations

from moonlight.setup.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.setup.calibration_steps import CalibrationSteps

ALL = {name: (i * 10, i * 10, 5, 5) for i, name in enumerate(REGIONS)}


def _keep_all(steps: CalibrationSteps) -> None:
    while not steps.in_review:
        assert steps.keep()


def test_keep_every_found_box_saves_them_with_size_and_scale() -> None:
    steps = CalibrationSteps((800, 600), ALL, ui_scale=1.5)
    _keep_all(steps)
    p = steps.profile()
    assert {n: r.to_tuple for n, r in p.regions.items()} == ALL
    assert (p.window_size, p.ui_scale, p.landmark) == ((800, 600), 1.5, (0, 0))


def test_missing_area_must_be_drawn_before_keep() -> None:
    found = {n: b for n, b in ALL.items() if n != "hp_bar"}
    steps = CalibrationSteps((800, 600), found)
    assert steps.area == "hp_bar" and steps.box is None
    assert not steps.keep()
    steps.redraw((1, 2, 3, 4))
    assert steps.keep() and steps.area == "mp_bar"


def test_redraw_replaces_the_box_and_back_goes_to_the_previous_area() -> None:
    steps = CalibrationSteps((800, 600), ALL)
    steps.keep()
    steps.back()
    assert steps.area == "hp_bar"
    steps.redraw((7, 7, 7, 7))
    steps.keep()
    _keep_all(steps)
    assert steps.profile()["hp_bar"] == Region(7, 7, 7, 7)


def test_review_pick_fixes_one_area_then_returns_to_review() -> None:
    steps = CalibrationSteps((800, 600), ALL)
    _keep_all(steps)
    steps.pick("minimap")
    assert steps.area == "minimap"
    steps.redraw((1, 1, 9, 9))
    steps.keep()
    assert steps.in_review
    assert steps.profile()["minimap"] == Region(1, 1, 9, 9)


def test_back_from_review_goes_to_the_last_area() -> None:
    steps = CalibrationSteps((800, 600), ALL)
    _keep_all(steps)
    steps.back()
    assert steps.area == REGIONS[-1]


def test_partial_save_keeps_old_boxes_for_unconfirmed_areas() -> None:
    old = CalibrationProfile(
        landmark=(0, 0),
        regions={"hp_bar": Region(1, 1, 1, 1), "minimap": Region(2, 2, 2, 2)},
        window_size=(640, 480),
    )
    steps = CalibrationSteps((800, 600), ALL, old=old)
    steps.keep()  # hp_bar confirmed with the found box
    p = steps.partial_profile()
    assert p["hp_bar"].to_tuple == ALL["hp_bar"]
    assert p["minimap"] == Region(2, 2, 2, 2)
    assert "mp_bar" not in p
    assert p.window_size == (800, 600)


def test_boxes_are_saved_relative_to_the_landmark() -> None:
    steps = CalibrationSteps((800, 600), ALL, landmark=(5, 5))
    _keep_all(steps)
    assert steps.profile()["mp_bar"] == Region(5, 5, 5, 5)
