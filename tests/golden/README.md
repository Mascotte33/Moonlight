# tests/golden/ — real-frame golden set (T08, T28)

The ground-truth frame set for all real-frame testing.

## Contents
- **200 real Tibia client frames** (2560x1369 PNG) copied from `golde-screenshots/`
  (user data — never modify the originals). 5 characters, 6 sessions (Jul 5-16),
  uniform fixed client layout. Filenames carry the event label
  (LevelUp/SkillUp/Achievement/TreasureFound/BossDefeated/LowHealth).
- `labels.json` — version-1 label schema (see `tools/label_golden.py`), one entry per
  frame: `hp_percent`, `mp_percent`, `monster_count`, `selected_target`,
  `players_on_screen_count`, `position_x/y/z` (null = player not placeable in that
  frame). Labels come from INDEPENDENT measurement (see T08), never from the readers
  under test.

## Used by
- `tests/perf/*_golden.py` — the 4 golden suites (vitals/battle/minimap/floor).
- `tests/replay/test_replay_perception.py` — full-module replay on all 200 frames.
- `tests/perf/test_golden_smoke.py` — smoke pass across all modules.

## Run
```bash
python -m pytest tests/perf tests/replay -q
```
