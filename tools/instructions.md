# instructions.md — `tools/` (dev, benchmark, and labeling tools)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14.

## What this is
Developer tools: capture benchmarks, golden-frame labeling, env probes, and
the vendored `arduino-cli`. Tools are excluded from `ruff`/`mypy` strict mode
(see `pyproject.toml`).

## Status
| File | Task | Status |
|------|------|--------|
| `label_golden.py` | T07 | ✅ DONE (17.2 KB) — golden-frame labeling tool |
| `bench_capture.py` | T02 | ✅ DONE (10.3 KB) — capture FPS benchmark |
| `capture_bench_*.json` | T02 | ✅ DONE — benchmark result baselines (3 files) |
| `dxcam_*.py` | — | ✅ scratch scripts probing dxcam capture path |
| `probe_env.py`, `llm_load.py` | — | ✅ env/LLM-load probes |
| `arduino-cli/arduino-cli.exe` | — | ✅ vendored binary (37 MB, Windows) |

## How to use (common)
```bash
cd <repo-root>
python tools/label_golden.py      # label golden frames -> tests/golden/labels.json
python tools/bench_capture.py     # measure capture FPS (see docs/hw_budget.md)
tools/arduino-cli/arduino-cli.exe --version
```

## Agent notes
- `label_golden.py` output (`tests/golden/labels.json`) is the ground-truth
  dataset that `perception/` replay tests will validate against. Keep its
  schema stable; document it in `tests/instructions.md`.
- **Do not commit large binaries** except the intentionally vendored
  `arduino-cli.exe` (it is git-ignored by design? verify `.gitignore`).
- These are throwaway/dev helpers — no strict typing required, but keep them
  working; don't leave broken scripts in the folder.
