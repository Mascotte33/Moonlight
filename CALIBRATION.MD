# Calibration remake

Goal: the bot works on any Tibia window size and on servers that move or scale
their UI. The user clicks **Start calibration**, the bot finds every screen
area by itself, the user fixes any box it got wrong, and Start refuses to run
if the layout no longer matches.

## Start here (for agents)

1. Read **How it works today** and **Rules**. They apply to every task.
2. Do the tasks in order, C1 first. Each says which earlier task it needs.
3. One task = one commit. pytest, `ruff check` and `mypy moonlight tests` stay green.

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
- The repo holds no reference images yet (`data/templates/` does not exist).

---

## Rules

1. **One calibration.** Each click on Start calibration replaces the profile.
   No per-server or per-resolution profiles, no resolution dropdown.
2. **One finder per area.** `moonlight/setup/autofind.py` has a `FINDERS`
   list, one entry per area: `(region name, finder function)`. A finder takes
   a screenshot and returns a box or `None`. Add an area = add an entry and a
   function. Remove it = delete both. Finders don't call each other.
3. **Scale 1.0 changes nothing.** The profile gets a `ui_scale` number
   (default 1.0). Every reader multiplies its sizes by it. At 1.0 each reader
   behaves exactly as it does today, so the existing tests stay as they are.
4. **New profile fields are optional.** Old profiles without `ui_scale` or
   `window_size` still load.
5. **Keep it small.** No new dependencies. OpenCV `matchTemplate` and numpy
   are already installed.

---

## Tasks

### C1.F1.K1 — Measure from the game area
- What: `capture.py` returns the client area (`GetClientRect` + `ClientToScreen`), not `GetWindowRect`. Region offsets and click positions (`window_origin` in `bot/wiring.py`) both use it.
- Files: moonlight/perception/capture.py, tests/unit/test_capture.py
- Done when: a test with a fake window shows the origin skips the title bar. Note for the owner: recalibrate afterwards, old offsets include the title bar.

### C2.F1.K1 — Reference images (owner does this)
- What: crop, from a normal-scale client, one PNG per anchor into `data/templates/anchors/`: `stop_button.png`, `battle_list_header.png`, `minimap_frame.png`, `floor_indicator.png`. Agents: if a finder needs another image, say which one and stop; don't invent it.
- Files: data/templates/anchors/*.png
- Done when: the files exist.

### C2.F2.K1 — Finders
- What: new `moonlight/setup/autofind.py` with `FINDERS` (Rule 2) and `find_regions(screenshot) -> (found: dict[name, Rect], missing: list[name])`. One finder per name in `calibrate.REGIONS`. Anchored areas use `matchTemplate` on the C2.F1 images. HP/MP bars: find the bar color. Game grid: the area left of the side panels (15×11 tiles).
- Needs: C2.F1.K1
- Files: moonlight/setup/autofind.py, tests/unit/test_autofind.py
- Done when: on a saved screenshot fixture, every area is found within a few pixels of the hand-drawn box; a missing anchor lands in `missing`, never raises.

### C2.F3.K1 — Pre-filled calibration window
- What: `CalibrationApp` opens with the found boxes drawn. Enter accepts a box, dragging redraws it. Missing areas are drawn by hand as today. The landmark is the found stop button; no landmark click.
- Needs: C2.F2.K1
- Files: moonlight/setup/calibrate.py, tests/unit/test_calibrate.py
- Done when: with every area found, pressing Enter for each saves a valid profile; with one missing, the user draws only that one.

### C2.F4.K1 — Save the window size
- What: calibration saves the client area size as `window_size: [w, h]` in the profile (Rule 4).
- Needs: C1.F1.K1
- Files: moonlight/setup/calibrate.py, tests/unit/test_calibrate.py
- Done when: a saved profile has `window_size`; an old profile without it still loads.

### C3.F1.K1 — Measure the UI scale
- What: `ui_scale` = found stop button width ÷ `stop_button.png` width, rounded to 0.05, saved in the profile (Rule 3). The GUI shows it as an editable number (GUI_REMAKE.MD `T2.F7.K1`).
- Needs: C2.F2.K1
- Files: moonlight/setup/autofind.py, moonlight/setup/calibrate.py, tests/unit/
- Done when: a screenshot fixture upscaled 1.5× gives `ui_scale` 1.5.

### C3.F2.K1 — Battle list uses the scale
- What: multiply the battle constants by `profile.ui_scale` when building `BattleReader`.
- Needs: C3.F1.K1
- Files: moonlight/perception/battle.py, tests/unit/test_battle.py
- Done when: the existing fixture upscaled 1.5× with `ui_scale` 1.5 reads the same creatures and HP as at 1.0.

### C3.F3.K1 — Minimap uses the scale
- What: scale `RADAR_WIDTH/HEIGHT` and `PLAYER_CENTER_X/Y`; pixels per tile = `ui_scale`.
- Needs: C3.F1.K1
- Files: moonlight/perception/minimap.py, tests/unit/test_minimap.py
- Done when: same check as C3.F2.K1, for position and movement.

### C3.F4.K1 — Templates use the scale
- What: floor, sprite and loot templates are resized by `ui_scale` once, when loaded.
- Needs: C3.F1.K1
- Files: moonlight/perception/floor.py, moonlight/perception/sprites.py, moonlight/perception/loot.py, tests/unit/
- Done when: same check as C3.F2.K1, for each reader. Owner then tests on real scaled servers and tunes `ui_scale` by hand if needed.

### C4.F1.K1 — Check the layout at Start
- What: before the loop starts, check the client size equals `window_size` and the stop button is still where the profile says. If not, refuse to start with "Layout changed, recalibrate". Profiles without `window_size` skip the size check.
- Needs: C2.F4.K1
- Files: moonlight/gui/loop_factory.py, moonlight/setup/autofind.py, tests/unit/test_loop_factory.py
- Done when: a test with a resized fake window gets the refusal; a matching window starts.

### C4.F2.K1 — Repeat the check while running (later)
- What: run the C4.F1 check every few seconds; pause the bot if it fails.
- Needs: C4.F1.K1
- Done when: do not build yet. First get C1–C4.F1 working on real servers.
