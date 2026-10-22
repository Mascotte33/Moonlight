# docs/

Research and reference documents for MOONLIGHT. Mostly read by agents. Change them only for research tasks.

| File | Purpose |
|------|---------|
| `ui_layout.md` | Tibia client UI layout: bars, battle list, minimap, floor, icons, containers. Source of truth for region geometry used by `moonlight/setup/calibrate.py` and `moonlight/perception/`. If the UI changes, update it, then re-run calibration |
| `hw_budget.md` | Capture FPS ceiling and CPU/GPU budget. Defines the 20 Hz capture budget |
| `perf_profile.md` | Pipeline performance profile |
| `senior_review_findings.md` | Senior review findings (F-xx) |

`ROUTER.md` and the `tasks/` folder are owned by another workflow. Do not edit `tasks/`.

## Do not

- The bot hardware is low-spec and CPU-only. The RTX 4090 is for running agents only. Keep proposals in `hw_budget.md` inside the CPU-only budget.
- Cite sources in research docs.
