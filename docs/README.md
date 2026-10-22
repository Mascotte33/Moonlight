# docs/

Research and reference documents for MOONLIGHT. Mostly read by agents. Change them only for research tasks.

| File | Purpose |
|------|---------|
| `ui_layout.md` | Tibia client UI layout: bars, battle list, minimap, floor, icons, containers. Source of truth for region geometry used by `moonlight/setup/calibrate.py` and `moonlight/perception/`. If the UI changes, update it, then re-run calibration |
| `hw_budget.md` | Capture FPS ceiling and CPU/GPU budget. Defines the 20 Hz capture budget |
| `ROUTER.md` | How the session running a PR picks tasks, briefs agents and commits |
| `prs/` | One doc per PR: goal, concepts, graph, task list, owner checks |
| `tasks/` | One file per task, grouped by PR, with the agent's brief |

## Do not

- The bot hardware is low-spec and CPU-only. The RTX 4090 is for running agents only. Keep proposals in `hw_budget.md` inside the CPU-only budget.
- Cite sources in research docs.
