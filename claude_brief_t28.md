# T28 — Make the golden-frame REPLAY tests actually RUN and PASS on 200 real Tibia frames

You are building/fixing the **T28** task in the MOONLIGHT repo (a Tibia screen-reader bot).
Work in this repo. Run your own tests/lint freely. Do NOT `git commit` — the orchestrator reviews and commits.

## What T28 is (from PROGRESS.md)
> `| T28 | qa | Replay tests for every module from recorded videos | T14..T27.D |`

Goal: validate every perception reader against **real, human-labeled game frames** (not synthetic
frames). The replay harness already exists in `tests/perf/*_golden.py` and **currently SKIPS** because
of two gaps you must close. When done, `python -m pytest tests/perf/ -rs` must RUN the golden suites
against the real frames and report real accuracy (not skip).

## Ground truth you already have (I verified these — trust them)
1. **Frames are in place.** 200 real Tibia screenshots now live in `tests/golden/*.png`.
   They are **2560 x 1369** (the user's real game resolution). Do not re-copy; they are there.
2. **Labels exist.** `tests/golden/labels.json` (200 frames) with fields:
   `hp_percent, mp_percent, monster_count, selected_target, players_on_screen_count, position_x,
   position_y, position_z` (and occasionally `notes`). `position_*` is often `null` (minimap couldn't
   place the player in that frame) — treat nulls as "skip that assertion", not "expected 0".
3. **Existing replay suites** (all currently skip): `tests/perf/test_vitals_golden.py` (3),
   `test_battle_golden.py` (4), `test_minimap_golden.py` (3), `test_floor_golden.py` (2). They skip on:
   "no profile with a <rect> rect -- run tools/calibrate.py first".

## The TWO blockers you must fix
### Blocker A — profile path mismatch
The golden tests hardcode `PROFILE_DIR = REPO_ROOT / "profiles"` and skip because `<repo>/profiles/`
does not exist. The real profile the bot uses lives in **`moonlight/profiles/`** (see
`moonlight.calibrate.default_profiles_dir()`). Make the golden suites read profiles from the SAME
directory the app uses (point PROFILE_DIR at `moonlight/profiles/`, or better, call
`default_profiles_dir()`). Do NOT leave a dead `<repo>/profiles/` path. Keep it consistent across all
four golden test files.

### Blocker B — the profile is missing 3 of the 5 regions
`moonlight/profiles/moonlight_user.json` currently has only:
```
"regions": { "hp_bar": [188,5,1001,12], "mp_bar": [1196,5,1000,12] }
```
Region format is **`(x, y, width, height)`** (see `moonlight/calibrate.py` `Rect` and
`moonlight/perception/vitals.py`). It is MISSING the regions the other suites need:
- `battle_list`  (needed by test_battle_golden) — Tibia's combat list, down the LEFT edge of the screen.
- `minimap`      (needed by test_minimap_golden) — the minimap, top-RIGHT corner.
- `floor_indicator` (needed by test_floor_golden) — the floor "N/M" readout (see moonlight/perception/floor.py
  for exactly which pixels it reads and where it expects them).

**Calibrate these three rects against the REAL 2560x1369 frames.** Open a few representative frames
(you can view PNGs directly, e.g. with PIL or by reading the image), and find the pixel boxes for the
battle list, the minimap, and the floor readout. Tibia's UI layout is fixed for this client, so one
good measurement applies to all 200 frames. Add the measured rects to `moonlight_user.json` (keep the
existing hp_bar/mp_bar — they're already correct for this layout). These regions are used by the LIVE
bot too, so prefer fixing the real profile over inventing a throwaway one; but if a coordinate is
genuinely uncertain, it's fine to create a dedicated `moonlight/profiles/golden.json` for the tests and
have the suites prefer it.

## How to decide when a frame FAILS (be a QA, not a rubber stamp)
Once the suites run, some frames may fail. For each failure decide and act:
- **Real perception bug** (reader miscounts monsters, misreads a bar, off-by-one, wrong color band,
  crash) → **fix the READER** (`moonlight/perception/*.py`), with a unit test that reproduces it.
- **Genuinely wrong tolerance** in a test → adjust it, but keep it tight and add a comment saying why.
- **Ambiguous / bad label** (the human label is clearly wrong, or the frame is a layout mismatch) →
  note it (the suites already honor a `LAYOUT_MISMATCH_NOTE` prefix in `notes`); do NOT silently "fix"
  a label. Leave a short note in the report.
Never make a test pass by hardcoding an expected value to the label or by broadening tolerance until it
goes green without understanding the failure.

## Extend "every module" as far as the labels allow
Strict accuracy needs ground-truth labels; the labels only cover **vitals (hp/mp), battle
(monster_count/selected_target/players), minimap (position)**. So:
- vitals, battle, minimap (and floor, per its existing test) → strict accuracy suites (make them run + pass).
- The other readers (`sprites`, `cursor`, `loot`, `logocr`) have NO per-frame labels. Add **golden
  smoke tests** for them: run each reader over the 200 frames and assert it (a) doesn't crash and
  (b) returns well-formed output of the right type/shape. Put these in `tests/perf/` alongside the
  accuracy suites. Document clearly that these are smoke coverage, not accuracy coverage.
Do not fabricate labels for sprite/cursor/loot/logocr.

## Acceptance criteria (you must hit ALL before declaring done)
1. `python -m pytest tests/perf/ -rs` → the vitals/battle/minimap/floor golden suites **RUN** (not
   skip) against the 200 frames and PASS. Print the per-module accuracy (e.g. mean abs error for
   hp/mp; exact-match rate for monster_count/selected_target/players; how many minimap frames had a
   non-null position and how many matched).
2. Golden smoke tests for `sprites`, `cursor`, `loot`, `logocr` added and passing.
3. Full unit suite still green: `python -m pytest -q` (no new failures vs the 811 baseline — you may
   ADD tests; don't drop existing ones).
4. `ruff check .` clean and `mypy .` clean.
5. `moonlight/profiles/moonlight_user.json` (or a dedicated golden profile) now carries
   hp_bar, mp_bar, battle_list, minimap, floor_indicator — all five regions.
6. Docs reconciled: update `tests/perf/README.md` (it still says "T08 pending / 12 skips") and any
   `instructions.md` that claims the golden data is pending — reflect that frames+labels are present
   and the suites run, with the real accuracy numbers.

## Housekeeping
- Leave NO scratch files in the repo (no briefs, logs, temp images). `tests/golden/*.png` are
  gitignored (there's a `tests/golden/.gitignore` with `*.png`) — that's correct, leave it.
- Do NOT `git commit`. Leave the working tree for the orchestrator.
- End with a concise report: what you changed (files), the per-module accuracy numbers, which frames
  still fail and why, and any label/layout caveats.
