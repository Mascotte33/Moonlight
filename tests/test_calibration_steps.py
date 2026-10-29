"""Calibration steps: keep, redraw, back, review jump, saves."""

from moonlight.calibration.calibrate import CalibrationProfile, Region
from moonlight.calibration.calibration_steps import AREAS as REGIONS
from moonlight.calibration.calibration_steps import CalibrationSteps

FOUND = {n: (i, i, 10, 10) for i, n in enumerate(REGIONS)}


def review(steps: CalibrationSteps) -> None:
    for _ in REGIONS:
        steps.keep()


def test_keep_walks_to_review() -> None:
    s = CalibrationSteps((100, 50), FOUND)
    assert s.area == REGIONS[0] and s.box == FOUND[REGIONS[0]]
    s.keep()
    assert s.area == REGIONS[1]
    for _ in REGIONS[1:]:
        s.keep()
    assert s.in_review and s.area is None and s.can_save


def test_redraw_replaces_box() -> None:
    s = CalibrationSteps((100, 50), FOUND)
    s.redraw((5, 6, 7, 8))
    assert s.boxes[REGIONS[0]] == (5, 6, 7, 8) and s.area == REGIONS[1]


def test_back_and_first_area_noop() -> None:
    s = CalibrationSteps((100, 50), FOUND)
    s.back()
    assert s.area == REGIONS[0]
    s.keep()
    s.back()
    assert s.area == REGIONS[0]


def test_review_jump_returns_to_review() -> None:
    s = CalibrationSteps((100, 50), FOUND)
    review(s)
    s.pick(REGIONS[3])
    assert s.area == REGIONS[3]
    s.redraw((1, 1, 2, 2))
    assert s.in_review and s.boxes[REGIONS[3]] == (1, 1, 2, 2)
    s.pick(REGIONS[2])
    s.keep()
    assert s.in_review
    s.pick(REGIONS[2])
    s.back()
    assert s.in_review


def test_full_save() -> None:
    s = CalibrationSteps((100, 50), FOUND, landmark=(3, 4))
    s.ui_scale = 1.5
    review(s)
    p = s.save()
    assert p.window_size == (100, 50) and p.ui_scale == 1.5 and p.landmark == (3, 4)
    assert p.regions[REGIONS[1]] == Region(1, 1, 10, 10) and len(p.regions) == len(REGIONS)


def test_full_save_blocked_before_review_or_missing_box() -> None:
    s = CalibrationSteps((100, 50), {})
    try:
        s.save()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    s.keep()  # no box: stays put
    assert s.area == REGIONS[0]


def test_partial_save_uses_confirmed_plus_old() -> None:
    old_regions = {REGIONS[1]: Region(9, 9, 9, 9), REGIONS[0]: Region(8, 8, 8, 8)}
    old = CalibrationProfile((0, 0), old_regions)
    s = CalibrationSteps((100, 50), FOUND, old)
    s.keep()  # hp_bar confirmed; the found-but-unconfirmed boxes are ignored
    p = s.save_partial()
    assert p.window_size == (100, 50)
    assert p.regions == {REGIONS[0]: Region(0, 0, 10, 10), REGIONS[1]: Region(9, 9, 9, 9)}
    assert CalibrationSteps((1, 1), FOUND).save_partial().regions == {}


def test_next_prev_move_without_confirming() -> None:
    s = CalibrationSteps((100, 50), FOUND)
    s.prev()
    assert s.area == REGIONS[0]
    s.next()
    s.next()
    assert s.area == REGIONS[2] and not s.confirmed and s.boxes == FOUND
    s.prev()
    assert s.area == REGIONS[1]
    for _ in REGIONS[2:]:
        s.next()
    assert s.area == REGIONS[-1]
    s.next()  # past the last area: the review
    assert s.in_review


def test_next_prev_do_nothing_in_review_or_review_jump() -> None:
    s = CalibrationSteps((100, 50), FOUND)
    review(s)
    s.next()
    s.prev()
    assert s.in_review
    s.pick(REGIONS[3])
    s.next()
    assert s.area == REGIONS[3]
    s.prev()
    assert s.area == REGIONS[3]
