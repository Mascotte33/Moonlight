"""Unit tests for SpriteDetector (monster sprite template matching).

These tests validate the sprite-detection *algorithm* entirely offline.
Real sprite templates come from user screenshots (T08), which are not
available yet, so the tests synthesise small, structured sprite templates
and render them into synthetic frames at known positions. The detector
must then find exactly those sprites at exactly those coordinates.

Why synthetic data is the right choice here
-------------------------------------------
* Template matching is a pure image operation on pixel arrays. As long as
  a known sprite pattern is rendered at a known pixel position, the
  detector's behaviour (detection, coordinates, NMS, thresholds) is fully
  determinable without a real game window.
* Real Tibia sprites are small (roughly 16-40 px). Synthetic 20x20
  patterns are the same order of magnitude, so the algorithm exercises the
  same code paths it would in production.
* The synthetic templates are *structured* (dense, per-monster unique)
  rather than a single bright block, so they behave like real sprites:
  the correct monster's template matches strongly, a different monster's
  template matches weakly, and two animation variants of the same monster
  both match the same on-screen sprite (the NMS case).

Conventions (shared with test_floor.py and test_minimap.py)
------------------------------------------------------------
* Frames are RGB ``(H, W, 3)`` uint8 arrays, as produced by
  ``moonlight.perception.capture``.
* Sprite templates are 2-D grayscale ``(H, W)`` uint8 arrays.
* ``REGION`` is the synthetic game-grid region in frame coordinates.
"""

import time

import cv2
import numpy as np
import pytest

from moonlight.calibration.calibrate import CalibrationProfile, Region
from moonlight.perception.sprites import (
    DEFAULT_MATCH_THRESHOLD,
    MonsterTemplates,
    SpriteDetection,
    SpriteDetector,
)

# --------------------------------------------------------------------------
# Synthetic scene constants
# --------------------------------------------------------------------------

SPRITE_H, SPRITE_W = 20, 20
ON = 230

# A realistic-looking game-grid region inside the frame.
REGION = (50, 40, 200, 160)

FRAME_H, FRAME_W = 240, 320


def make_template(monster_idx: int, variant: int = 0) -> np.ndarray:
    """Return a synthetic 20x20 grayscale sprite for a monster.

    The base pattern is a dense, per-monster unique layout (seeded by
    *monster_idx*). A *variant* flips a handful of pixels, simulating an
    animation frame: close enough that both variants match the same
    on-screen sprite, different enough that they are not identical.
    """
    rng = np.random.RandomState(monster_idx * 1000 + 7)
    image = np.zeros((SPRITE_H, SPRITE_W), dtype=np.uint8)
    image[rng.rand(SPRITE_H, SPRITE_W) < 0.45] = ON
    if variant:
        vrng = np.random.RandomState(monster_idx * 100 + 123)
        for _ in range(5):
            yy, xx = int(vrng.randint(SPRITE_H)), int(vrng.randint(SPRITE_W))
            image[yy, xx] = 0 if image[yy, xx] else ON
    return image


def make_frame(sprites: list[tuple[int, int, int, int]]) -> np.ndarray:
    """Render an RGB frame with synthetic sprites pasted at known positions.

    Args:
        sprites: List of ``(monster_idx, x, y, variant)``. Each sprite is
            pasted at its top-left ``(x, y)`` in frame coordinates.

    Returns:
        RGB ``(H, W, 3)`` uint8 array.
    """
    frame = np.full((FRAME_H, FRAME_W, 3), 5, dtype=np.uint8)
    for midx, x, y, variant in sprites:
        template = make_template(midx, variant)
        frame[y:y + SPRITE_H, x:x + SPRITE_W] = cv2.cvtColor(
            template, cv2.COLOR_GRAY2RGB
        )
    return frame


def detector(monsters: dict[int, int]) -> SpriteDetector:
    """Build a detector for the default REGION and given monsters.

    Args:
        monsters: Mapping of monster_idx -> variant to template with.
    """
    templates = {
        f"monster_{midx}": [make_template(midx, variant)]
        for midx, variant in monsters.items()
    }
    return SpriteDetector(game_grid_region=REGION, monster_templates=templates)


# --------------------------------------------------------------------------
# Core detection
# --------------------------------------------------------------------------


def test_detect_single_sprite_at_exact_position() -> None:
    """A sprite at a known position is found at exactly that position."""
    x, y = 80, 70
    frame = make_frame([(0, x, y, 0)])
    det = detector({0: 0})

    detections = det.detect(frame)

    assert len(detections) == 1
    hit = detections[0]
    assert hit.monster == "monster_0"
    assert hit.x == x
    assert hit.y == y
    assert hit.width == SPRITE_W
    assert hit.height == SPRITE_H
    assert hit.confidence >= 0.99


def test_detect_positions_are_frame_coordinates() -> None:
    """Reported positions include the region offset (frame coords, not crop)."""
    x, y = 120, 90
    frame = make_frame([(0, x, y, 0)])
    det = detector({0: 0})

    detections = det.detect(frame)

    assert len(detections) == 1
    # If the region offset were dropped, x would be 120 - REGION[0] = 70.
    assert detections[0].x == x
    assert detections[0].y == y


def test_detect_two_different_monsters() -> None:
    """Two different monsters are both found at their correct positions."""
    frame = make_frame([(0, 70, 60, 0), (1, 150, 100, 0)])
    det = detector({0: 0, 1: 0})

    detections = det.detect(frame)

    by_monster = {d.monster: d for d in detections}
    assert set(by_monster) == {"monster_0", "monster_1"}
    assert by_monster["monster_0"].x == 70
    assert by_monster["monster_0"].y == 60
    assert by_monster["monster_1"].x == 150
    assert by_monster["monster_1"].y == 100


def test_detect_two_instances_of_same_monster() -> None:
    """Two instances of the same monster type are reported separately."""
    frame = make_frame([(0, 70, 60, 0), (0, 150, 110, 0)])
    det = detector({0: 0})

    detections = det.detect(frame)

    assert len(detections) == 2
    positions = sorted((d.x, d.y) for d in detections)
    assert positions == [(70, 60), (150, 110)]


def test_blank_frame_returns_empty() -> None:
    """A frame with no sprites yields no detections."""
    frame = np.full((FRAME_H, FRAME_W, 3), 5, dtype=np.uint8)
    det = detector({0: 0})

    assert det.detect(frame) == []


def test_confidence_in_valid_range() -> None:
    """Every reported confidence lies in the (0, 1] normalized range."""
    frame = make_frame([(0, 70, 60, 0), (1, 150, 100, 0)])
    det = detector({0: 0, 1: 0})

    for hit in det.detect(frame):
        assert 0.0 < hit.confidence <= 1.0


# --------------------------------------------------------------------------
# Non-maximum suppression
# --------------------------------------------------------------------------


def test_nms_collapses_identical_frames() -> None:
    """Two identical template frames matching the same sprite -> one hit."""
    t = make_template(0, 0)
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={"monster_0": [t, t]},
    )
    frame = make_frame([(0, 80, 70, 0)])

    assert len(det.detect(frame)) == 1


def test_nms_collapses_animation_variants() -> None:
    """Two animation frames matching the same sprite -> one hit, not two."""
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={
            "monster_0": [make_template(0, 0), make_template(0, 1)]
        },
    )
    # Sprite drawn from variant 1; both variants match this same sprite.
    frame = make_frame([(0, 80, 70, 1)])

    detections = det.detect(frame)
    assert len(detections) == 1
    assert detections[0].x == 80
    assert detections[0].y == 70


def test_nms_keeps_distinct_instances() -> None:
    """NMS suppresses duplicates, not genuinely separate sprites."""
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={
            "monster_0": [make_template(0, 0), make_template(0, 1)]
        },
    )
    frame = make_frame([(0, 70, 60, 0), (0, 160, 110, 1)])

    detections = det.detect(frame)
    assert len(detections) == 2
    positions = sorted((d.x, d.y) for d in detections)
    assert positions == [(70, 60), (160, 110)]


# --------------------------------------------------------------------------
# Threshold behaviour
# --------------------------------------------------------------------------


def test_strict_threshold_rejects_imperfect_match() -> None:
    """A threshold above the match score filters the sprite out."""
    # Template is variant 0; sprite is variant 1 (imperfect match, ~0.97).
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={"monster_0": [make_template(0, 0)]},
        match_threshold=0.999999,
    )
    frame = make_frame([(0, 80, 70, 1)])

    assert det.detect(frame) == []


def test_lenient_threshold_accepts_cross_variant_match() -> None:
    """A low threshold accepts the cross-variant (imperfect) match."""
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={"monster_0": [make_template(0, 0)]},
        match_threshold=0.5,
    )
    frame = make_frame([(0, 80, 70, 1)])

    detections = det.detect(frame)
    assert len(detections) == 1
    assert detections[0].x == 80


def test_default_threshold_suppresses_cross_monster_false_positives() -> None:
    """With the default threshold, only the true monster is detected."""
    frame = make_frame([(1, 150, 100, 0)])
    # Detector only knows monster 0's template; monster 1's sprite must NOT
    # match it above the default threshold.
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={"monster_0": [make_template(0, 0)]},
        match_threshold=DEFAULT_MATCH_THRESHOLD,
    )

    assert det.detect(frame) == []


# --------------------------------------------------------------------------
# Region handling
# --------------------------------------------------------------------------


def test_no_region_returns_empty() -> None:
    """A detector with no region knows nothing to scan -> no detections."""
    det = SpriteDetector(
        monster_templates={"monster_0": [make_template(0, 0)]}
    )
    frame = make_frame([(0, 80, 70, 0)])

    assert det.game_grid_region is None
    assert det.detect(frame) == []


def test_region_outside_frame_returns_empty() -> None:
    """A region entirely outside the frame yields no detections."""
    det = SpriteDetector(
        game_grid_region=(500, 400, 200, 160),
        monster_templates={"monster_0": [make_template(0, 0)]},
    )
    frame = make_frame([(0, 80, 70, 0)])

    assert det.detect(frame) == []


def test_region_clipped_to_frame() -> None:
    """A region extending past the frame edge still detects edge sprites."""
    # Region starts left of the frame; sprite sits near the visible edge.
    det = SpriteDetector(
        game_grid_region=(-20, 40, 240, 160),
        monster_templates={"monster_0": [make_template(0, 0)]},
    )
    frame = make_frame([(0, 30, 70, 0)])

    detections = det.detect(frame)
    assert len(detections) == 1
    assert detections[0].x == 30
    assert detections[0].y == 70


def test_explicit_region_overrides_profile() -> None:
    """An explicit region wins over the profile's game_grid region."""
    profile = CalibrationProfile(landmark=(0, 0))
    profile.set_region("game_grid", Region(*REGION))
    det = SpriteDetector(profile, game_grid_region=REGION)

    assert det.game_grid_region == REGION


def test_profile_region_used_as_default() -> None:
    """The profile's game_grid region is used when no explicit region given."""
    profile = CalibrationProfile(landmark=(0, 0))
    profile.set_region("game_grid", Region(*REGION))
    det = SpriteDetector(profile, monster_templates={"monster_0": [make_template(0, 0)]})

    assert det.game_grid_region == REGION
    frame = make_frame([(0, 80, 70, 0)])
    assert len(det.detect(frame)) == 1


def test_invalid_region_raises() -> None:
    """A malformed region is rejected at construction."""
    with pytest.raises(ValueError):
        SpriteDetector(game_grid_region=(0, 0, 100))  # type: ignore[arg-type]  # 3-tuple


# --------------------------------------------------------------------------
# Template configuration
# --------------------------------------------------------------------------


def test_no_templates_returns_empty() -> None:
    """No templates configured -> nothing can match, even if sprites exist."""
    det = SpriteDetector(game_grid_region=REGION)
    frame = make_frame([(0, 80, 70, 0)])

    assert det.monsters == ()
    assert det.detect(frame) == []


def test_dict_input_accepted() -> None:
    """monster_templates as a {name: [frames]} dict is accepted."""
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={"monster_0": [make_template(0, 0)]},
    )
    frame = make_frame([(0, 80, 70, 0)])

    assert det.monsters == ("monster_0",)
    assert len(det.detect(frame)) == 1


def test_monster_templates_sequence_accepted() -> None:
    """monster_templates as a sequence of MonsterTemplates is accepted."""
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates=[MonsterTemplates("monster_0", [make_template(0, 0)])],
    )
    frame = make_frame([(0, 80, 70, 0)])

    assert len(det.detect(frame)) == 1


def test_invalid_templates_empty_frames_raises() -> None:
    """A monster with no template frames is rejected."""
    with pytest.raises(ValueError):
        MonsterTemplates("monster_0", [])


def test_invalid_templates_empty_name_raises() -> None:
    """A monster with an empty name is rejected."""
    with pytest.raises(ValueError):
        MonsterTemplates("", [make_template(0, 0)])


def test_invalid_templates_3d_raises() -> None:
    """A 3-D (RGB) template is rejected; templates must be grayscale."""
    rgb = np.zeros((SPRITE_H, SPRITE_W, 3), dtype=np.uint8)
    with pytest.raises(ValueError):
        MonsterTemplates("monster_0", [rgb])


def test_oversized_template_skipped() -> None:
    """A template larger than the region is skipped without error."""
    big = np.zeros((300, 300), dtype=np.uint8)
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={"monster_0": [big]},
    )
    frame = make_frame([(0, 80, 70, 0)])

    assert det.detect(frame) == []


# --------------------------------------------------------------------------
# Helpers and performance
# --------------------------------------------------------------------------


def test_center_property() -> None:
    """The center property is the midpoint of the matched box."""
    hit = SpriteDetection(
        monster="m", x=10, y=20, width=SPRITE_W, height=SPRITE_H, confidence=1.0
    )
    assert hit.center == (10 + SPRITE_W // 2, 20 + SPRITE_H // 2)
    assert hit.to_rect == (10, 20, SPRITE_W, SPRITE_H)


def test_performance_within_budget() -> None:
    """Detection stays far below the 10 ms perception budget (T30)."""
    det = SpriteDetector(
        game_grid_region=REGION,
        monster_templates={
            f"monster_{i}": [make_template(i, 0), make_template(i, 1)]
            for i in range(4)
        },
    )
    frame = make_frame([(0, 70, 60, 0), (1, 150, 100, 1)])

    start = time.perf_counter()
    det.detect(frame)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert elapsed_ms < 10.0, f"detect() took {elapsed_ms:.2f} ms (budget 10 ms)"


# --------------------------------------------------------------------------
# P3: on-demand scope (coarse-to-fine, ROI around the player, template cap)
# --------------------------------------------------------------------------


def pixel_art(seed: int) -> np.ndarray:
    """A 20x20 sprite of 2x2-pixel blocks (Tibia-like pixel art, unlike noise)."""
    rng = np.random.RandomState(seed)
    blocks = np.where(rng.rand(10, 10) < 0.45, ON, 0).astype(np.uint8)
    return np.kron(blocks, np.ones((2, 2), dtype=np.uint8))


@pytest.mark.parametrize("x, y", [(80, 70), (81, 71), (133, 97), (51, 41)])
def test_coarse_pass_finds_odd_and_even_offsets_exactly(x: int, y: int) -> None:
    """The 2x coarse pass loses nothing: same hits as full resolution."""
    sprites = [pixel_art(1), pixel_art(2)]
    frame = np.full((FRAME_H, FRAME_W, 3), 5, dtype=np.uint8)
    for (px, py), sprite in zip([(x, y), (180, 140)], sprites, strict=True):
        frame[py:py + SPRITE_H, px:px + SPRITE_W] = sprite[..., None]
    templates = {f"monster_{i}": [sprites[i]] for i in range(2)}
    fine = SpriteDetector(game_grid_region=REGION, monster_templates=templates, downscale=1)
    coarse = SpriteDetector(game_grid_region=REGION, monster_templates=templates)
    hits = {(d.monster, d.x, d.y) for d in coarse.detect(frame)}
    assert hits == {(d.monster, d.x, d.y) for d in fine.detect(frame)}
    assert ("monster_0", x, y) in hits


def test_near_player_searches_only_the_grid_centre() -> None:
    """near_player=True skips sprites outside the central half of the grid."""
    # REGION centre half: x 100..200, y 80..160.
    frame = make_frame([(0, 130, 100, 0), (0, 56, 46, 0)])
    det = detector({0: 0})
    assert len(det.detect(frame)) == 2
    assert [(d.x, d.y) for d in det.detect(frame, near_player=True)] == [(130, 100)]


def test_monster_filter_and_template_cap() -> None:
    """Only the named monsters are matched, and at most max_templates frames."""
    frame = make_frame([(0, 70, 60, 0), (1, 160, 110, 0)])
    templates = {f"monster_{i}": [make_template(i, 0)] for i in range(2)}
    det = SpriteDetector(game_grid_region=REGION, monster_templates=templates)
    assert {d.monster for d in det.detect(frame, monsters={"monster_1"})} == {"monster_1"}
    capped = SpriteDetector(
        game_grid_region=REGION, monster_templates=templates, max_templates=1
    )
    assert {d.monster for d in capped.detect(frame)} == {"monster_0"}
    with pytest.raises(ValueError):
        SpriteDetector(max_templates=0)


def test_ui_scale_detects_enlarged_sprites() -> None:
    """At UI scale 1.5 the templates grow with the client and still hit."""
    frame = make_frame([(0, 70, 60, 0), (1, 160, 110, 0)])
    big = cv2.resize(frame, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_NEAREST)
    profile = CalibrationProfile(ui_scale=1.5)
    profile.set_region("game_grid", Region(75, 60, 300, 240))
    templates = {f"monster_{i}": [make_template(i, 0)] for i in range(2)}
    det = SpriteDetector(profile, monster_templates=templates)
    hits = {(d.monster, d.x, d.y, d.width) for d in det.detect(big)}
    assert hits == {("monster_0", 105, 90, 30), ("monster_1", 240, 165, 30)}
