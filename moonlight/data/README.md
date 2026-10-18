# data/ - dataset storage

Local, git-ignored data storage for MOONLIGHT.

## golden/ - golden-frame dataset (T08, pending)

Human-labelled screenshots the `perception/` golden tests validate against:

- `golden/*.png` — captured client frames (~200 planned)
- `golden/labels.json` — per-frame labels (HP/MP %, battle entries, minimap
  visibility, floor level) written by `tools/label_golden.py`

The perf tests (`tests/perf/`) read from `data/golden/` (preferred) or
`tests/golden/` and skip cleanly until this data exists.

## Status

- ⚠️ **Empty** — waiting on T08: the user records screenshots/videos and
  labels them with `tools/label_golden.py`.
