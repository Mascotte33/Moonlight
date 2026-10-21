# Calibration remake

Goal: the bot works on any Tibia window size and on servers that move or scale
their UI. The user clicks **Start calibration**, the bot finds every screen
area by itself, the user fixes any box it got wrong, and Start refuses to run
if the layout no longer matches.

## Start here (for agents)

1. Read **How it works today**, **Data shapes** and **Rules**. They apply to every task.
2. Do the tasks in order. Each says which earlier task it **Needs**.
3. Touch only the task's **Files**. **Don't touch** lists what is off limits.
4. One task = one commit. pytest, `ruff check` and `mypy moonlight tests` stay green.

**Stop and ask the owner** (don't work around it) when:
- a reference image or golden screenshot the task needs is missing;
- the **Done when** check can't be met as written;
- the change needs a file outside the task's **Files**.

---

## How it works today

- `moonlight/setup/calibrate.py`: an OpenCV window. The user clicks a landmark,
  then drags one box per area in `REGIONS`. Saved to `profiles/<name>.json`.
- At runtime the boxes are fixed pixel offsets from the window's top-left
  corner (`bot/wiring.py`). The landmark is never searched for again.
- That corner comes from `GetWindowRect` in `perception/capture.py`, which
  includes the title bar and borders.
- HP/MP and the game grid scale with their box. These readers assume fixed
  pixel sizes and break on a scaled UI:
  - `perception/battle.py`: `SLOT_HEIGHT`, `CONTENT_WIDTH`, `TOP_PADDING`, `NAME_START_X`, `HP_BAR_ROWS`
  - `perception/minimap.py`: `RADAR_WIDTH/HEIGHT`, `PLAYER_CENTER_X/Y`
  - `perception/floor.py`, `perception/sprites.py`, `perception/loot.py`: fixed-size template images
- Errors while building the bot are already caught in `gui/app.py`
  (`start_bot`) and logged as "could not start bot". Raise there; don't add popups.

**Already exists, reuse it:**
- `tools/calibrate_auto.py` → `detect_bars(rgb)` finds the HP and MP bar boxes.
  Checked on 200 screenshots. Note: it takes **RGB**, the rest of the code uses BGR.
- `tests/golden/*.png`: 202 full client screenshots (2560×1369). These are the
  test images for every finder.
- `profiles/moonlight_user.json`: the hand-drawn boxes for those screenshots
  (hp_bar, mp_bar, battle_list, minimap, floor_indicator). They are the
  expected answers.

**Not in the repo:**
- `tests/golden/*.png` is gitignored: the screenshots exist only on the owner's
  PC. Any test that uses them must skip when they are missing:
  `pytest.mark.skipif(not any(GOLDEN.glob("*.png")), reason="golden screenshots not present")`.
- `data/templates/` (reference images) does not exist yet. C2.F1.K1 adds it.

---

## Data shapes

| Thing | Shape |
|---|---|
| Box | `Rect = (x, y, w, h)`, the type in `setup/calibrate.py` |
| Screenshot | numpy `(H, W, 3)` uint8, **BGR** |
| Coordinates | physical pixels, relative to the game area's top-left (after C1) |
| `ui_scale` | `float` attribute on `CalibrationProfile`, default `1.0` |
| `window_size` | `tuple[int, int] \| None` attribute on `CalibrationProfile`, default `None` |

Both new fields are read in `CalibrationProfile.from_dict` and written in `to_dict`.

---

## Rules

1. **One calibration.** Each click on Start calibration replaces the profile.
   No per-server or per-resolution profiles, no resolution dropdown.
2. **One finder per area.** `moonlight/setup/autofind.py` has a `FINDERS`
   list, one entry per area: `(region name, finder function)`. A finder is
   `def find_<name>(screenshot: np.ndarray) -> Rect | None`. Add an area = add
   an entry, a function and a test. Remove it = delete all three. Finders
   don't call each other.
3. **Scale 1.0 changes nothing.** Every reader multiplies its sizes by
   `ui_scale`. At 1.0 each reader behaves exactly as it does today, so the
   existing tests stay as they are.
4. **New profile fields are optional.** Old profiles without `ui_scale` or
   `window_size` still load.
5. **Keep it small.** No new dependencies. OpenCV `matchTemplate` and numpy
   are already installed.

---

## Tasks

### C1.F1.K1 — Measure from the game area
- What: `capture.py` returns the client area (`GetClientRect` + `ClientToScreen`), not `GetWindowRect`. Region offsets and click positions (`window_origin` in `bot/wiring.py`) both use it.
- Files: moonlight/perception/capture.py, tests/unit/test_capture.py
- Don't touch: profiles/*.json, the readers.
- Done when: `pytest tests/unit/test_capture.py` passes, with a test using a fake window that shows the origin skips the title bar.
- Warning: after this, the owner's current profile is off by the title bar height until they recalibrate. Say so in the commit message.

### C2.F1.K1 — Reference images (owner does this)
- What: crop, from a normal-scale client, one PNG per anchor into `data/templates/anchors/`: `stop_button.png`, `battle_list_header.png`, `minimap_frame.png`, `floor_indicator.png`. Also draw `game_grid` and `status_icons` once with today's calibration tool, so `profiles/moonlight_user.json` has expected answers for them.
- Files: data/templates/anchors/*.png, profiles/moonlight_user.json
- Done when: the files exist and the profile has all seven regions.

### C2.F2.K1 — Finder skeleton
- What: new `moonlight/setup/autofind.py` with an empty `FINDERS` list (Rule 2) and `find_regions(screenshot) -> tuple[dict[str, Rect], list[str]]`, returning found boxes and the names that weren't found. A finder that returns `None` or raises lands in the missing list.
- Files: moonlight/setup/autofind.py, tests/unit/test_autofind.py
- Don't touch: calibrate.py, the readers.
- Done when: `pytest tests/unit/test_autofind.py` passes, with tests using two fake finders, one that finds and one that fails.

### C2.F2.K2 — HP and MP bar finders
- What: `find_hp_bar` and `find_mp_bar`, built on `detect_bars` from `tools/calibrate_auto.py`. Move what's needed into `autofind.py` and make the tool import it from there. Convert BGR → RGB before calling.
- Needs: C2.F2.K1
- Files: moonlight/setup/autofind.py, tools/calibrate_auto.py, tests/unit/test_autofind.py
- Done when: `pytest tests/unit/test_autofind.py` passes; on the golden screenshots each box is within 3 px of `profiles/moonlight_user.json`; `python tools/calibrate_auto.py` still runs.

### C2.F2.K3 — Battle list finder
- What: `find_battle_list`: `matchTemplate` with `battle_list_header.png`, the box starts below the header.
- Needs: C2.F1.K1, C2.F2.K1
- Files: moonlight/setup/autofind.py, tests/unit/test_autofind.py
- Done when: on the golden screenshots, x, y and w are within 3 px of the profile's `battle_list`. The height depends on the window, so the test checks it is at least 58 px.

### C2.F2.K4 — Minimap finder
- What: `find_minimap`: `matchTemplate` with `minimap_frame.png`.
- Needs: C2.F1.K1, C2.F2.K1
- Files: moonlight/setup/autofind.py, tests/unit/test_autofind.py
- Done when: on the golden screenshots, within 3 px of the profile's `minimap`.

### C2.F2.K5 — Floor indicator finder
- What: `find_floor_indicator`: `matchTemplate` with `floor_indicator.png`.
- Needs: C2.F1.K1, C2.F2.K1
- Files: moonlight/setup/autofind.py, tests/unit/test_autofind.py
- Done when: on the golden screenshots, within 3 px of the profile's `floor_indicator`.

### C2.F2.K6 — Game grid and status icons finders
- What: `find_game_grid` (the game view left of the side panels; its width ÷ 15 must equal its height ÷ 11) and `find_status_icons`.
- Needs: C2.F1.K1, C2.F2.K1
- Files: moonlight/setup/autofind.py, tests/unit/test_autofind.py
- Done when: on the golden screenshots, within 3 px of the profile's `game_grid` and `status_icons`.

### C2.F3.K1 — Pre-filled calibration window
- What: `CalibrationApp` opens with the boxes from `find_regions` drawn. Enter accepts a box, dragging redraws it. Missing areas are drawn by hand as today. The landmark is the found stop button; no landmark click.
- Needs: C2.F2.K1 (works with however many finders exist)
- Files: moonlight/setup/calibrate.py, tests/unit/test_calibrate.py
- Don't touch: autofind.py finders.
- Done when: `pytest tests/unit/test_calibrate.py` passes. With every area found, pressing Enter for each saves a valid profile; with one missing, the user draws only that one.

### C2.F4.K1 — Save the window size
- What: calibration saves the client area size as `window_size` (see Data shapes, Rule 4).
- Needs: C1.F1.K1
- Files: moonlight/setup/calibrate.py, tests/unit/test_calibrate.py
- Done when: `pytest tests/unit/test_calibrate.py` passes; a saved profile has `window_size`; an old profile without it still loads.

### C3.F1.K1 — Measure the UI scale
- What: `ui_scale` = found stop button width ÷ `stop_button.png` width, rounded to 0.05, saved in the profile. The GUI shows it as an editable number (GUI_REMAKE.MD `T2.F7.K1`, not this task).
- Needs: C2.F1.K1, C2.F2.K1
- Files: moonlight/setup/autofind.py, moonlight/setup/calibrate.py, tests/unit/test_autofind.py
- Don't touch: the readers, the GUI.
- Done when: a golden screenshot gives `ui_scale` 1.0, and the same screenshot enlarged 1.5× with `cv2.resize` gives 1.5.

### C3.F2.K1 — Battle list uses the scale
- What: multiply the battle constants by `profile.ui_scale` when building `BattleReader`. Don't change the constants' values.
- Needs: C3.F1.K1
- Files: moonlight/perception/battle.py, tests/unit/test_battle.py
- Done when: `pytest tests/unit/test_battle.py` passes; a battle list crop enlarged 1.5× with `ui_scale` 1.5 reads the same creatures and HP as at 1.0.

### C3.F3.K1 — Minimap uses the scale
- What: scale `RADAR_WIDTH/HEIGHT` and `PLAYER_CENTER_X/Y`; pixels per tile = `ui_scale`. Don't change the constants' values.
- Needs: C3.F1.K1
- Files: moonlight/perception/minimap.py, tests/unit/test_minimap.py
- Done when: `pytest tests/unit/test_minimap.py` passes; the same 1.5× check as C3.F2.K1, for position and movement.

### C3.F4.K1 — Templates use the scale
- What: floor, sprite and loot templates are resized by `ui_scale` once, when loaded.
- Needs: C3.F1.K1
- Files: moonlight/perception/floor.py, moonlight/perception/sprites.py, moonlight/perception/loot.py, tests/unit/test_floor.py, tests/unit/test_sprites.py, tests/unit/test_looting.py
- Done when: those three test files pass, each with the same 1.5× check as C3.F2.K1. The owner then tests on real scaled servers and tunes `ui_scale` by hand if needed.

### C4.F1.K1 — Check the layout at Start
- What: before the loop starts, check the client size equals `window_size` and the stop button is still where the profile says. If not, raise `ValueError("Layout changed, recalibrate")` from `build_gui_loop`; `gui/app.py` already logs it. Profiles without `window_size` skip the size check.
- Needs: C2.F4.K1
- Files: moonlight/gui/loop_factory.py, moonlight/setup/autofind.py, tests/unit/test_loop_factory.py
- Don't touch: gui/app.py.
- Done when: `pytest tests/unit/test_loop_factory.py` passes; a fake window with a different size gets the error; a matching one starts.

### C4.F2.K1 — Repeat the check while running (later)
- What: run the C4.F1 check every few seconds; pause the bot if it fails.
- Needs: C4.F1.K1
- Done when: do not build yet. First get C1–C4.F1 working on real servers.
