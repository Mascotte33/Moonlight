#!/usr/bin/env python3
"""Auto-label HP%/MP% on golden frames and derive the hp_bar/mp_bar profile (T08).

Independent of ``perception/vitals.py`` on purpose (no circularity): the bar
box is found per frame from the UI itself, fill is classified by hue rather
than by VitalsReader's colour palette, and every bar row is used.

Bar structure in this client (verified on the 200 golden frames):

* HP and MP sit side by side in a top strip. HP fills left->right and changes
  colour with health (green / yellow-green / yellow / dark red); MP is blue
  and fills right->left. White ``cur/max`` text is drawn over both. With
  magic shield up, a purple strip covers the lower half of MP (not mana).
* Directly below the bars is a light-grey (~117-125) bottom border made of
  two long runs -- HP then MP -- separated by a short dark gap. That row gives
  each bar's x-extent; the rows above it that contain fill give the y-extent.

percent = fill columns / bar width * 100, where a column counts as filled if
any of its bar rows has a fill-coloured pixel (robust to the text overlay).

Usage:
    python tools/calibrate_auto.py [--dir tests/golden] [--profile profiles/moonlight_user.json]

Frames are only read, never modified.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
from label_golden import save_labels, validate_document  # noqa: E402

# doc: docs/prs/gui-calibration.md#bar-finders
from moonlight.calibration.autofind import detect_bars, hp_fill_mask, mp_fill_mask  # noqa: E402

LAYOUT_MISMATCH_NOTE = "layout-mismatch"  # read by tests/perf/test_vitals_golden.py

Box = tuple[int, int, int, int]  # x, y, w, h


def fill_percent(rgb: np.ndarray, box: Box, mask_fn: Callable[[np.ndarray], np.ndarray]) -> float:
    x, y, w, h = box
    cols = mask_fn(rgb[y:y + h, x:x + w]).any(axis=0)
    return round(100.0 * float(cols.sum()) / w, 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=REPO_ROOT / "tests" / "golden")
    parser.add_argument("--profile", type=Path, default=REPO_ROOT / "profiles" / "moonlight_user.json")
    args = parser.parse_args(argv)

    images = sorted(args.dir.glob("*.png"))
    if not images:
        print(f"no PNGs in {args.dir}", file=sys.stderr)
        return 2

    measured: dict[str, tuple[float, float, Box, Box]] = {}
    for path in images:
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            print(f"error: cannot decode {path.name}", file=sys.stderr)
            return 1
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.int16)
        try:
            hp_box, mp_box = detect_bars(rgb)
        except ValueError as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            return 1
        hp = fill_percent(rgb, hp_box, hp_fill_mask)
        mp = fill_percent(rgb, mp_box, mp_fill_mask)
        measured[path.name] = (hp, mp, hp_box, mp_box)

    # Profile = majority layout. Frames with another layout are flagged so the
    # single-profile golden test can skip them.
    layouts = collections.Counter((h, m) for _, _, h, m in measured.values())
    (hp_bar, mp_bar), n_major = layouts.most_common(1)[0]

    print(f"{'frame':<60} {'hp%':>7} {'mp%':>7}")
    frames: dict[str, dict[str, object]] = {}
    for name, (hp, mp, hb, mb) in measured.items():
        entry: dict[str, object] = {
            "hp_percent": hp,
            "mp_percent": mp,
            "monster_count": 0,
            "selected_target": None,
            "players_on_screen_count": 0,
            "position_x": None,
            "position_y": None,
            "position_z": None,
        }
        flag = ""
        if (hb, mb) != (hp_bar, mp_bar):
            entry["notes"] = f"{LAYOUT_MISMATCH_NOTE}: hp_bar={list(hb)} mp_bar={list(mb)}"
            flag = "  [other layout]"
        frames[name] = entry
        print(f"{name[:60]:<60} {hp:7.2f} {mp:7.2f}{flag}")

    doc = {"version": 1, "frames": frames}
    errors = validate_document(doc)
    if errors:
        print("labels failed validation:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    labels_path = args.dir / "labels.json"
    save_labels(labels_path, doc)

    profile = {"landmark": [0, 0], "regions": {"hp_bar": list(hp_bar), "mp_bar": list(mp_bar)}}
    args.profile.parent.mkdir(parents=True, exist_ok=True)
    args.profile.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")

    print(f"\n{len(frames)} frames -> {labels_path}")
    print(f"layout used by {n_major}/{len(frames)} frames: hp_bar={hp_bar} mp_bar={mp_bar} -> {args.profile}")
    for (h, m), n in layouts.most_common()[1:]:
        print(f"  other layout ({n} frame(s)): hp_bar={h} mp_bar={m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
