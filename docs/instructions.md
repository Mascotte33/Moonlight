# instructions.md — `docs/` (research & design docs)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14.

## What this is
Research, design, and hardware-budget documents. These are read-heavy: agents
consume them; only research tasks should change them.

## Status

| File | Task | Status |
|------|------|--------|
| `ui_layout.md` | T01 | ✅ DONE (19.9 KB) — client UI regions, landmarks, coordinates; from production bot + TibiaWiki |
| `hw_budget.md` | T02 | ✅ DONE (8.7 KB) — capture FPS + CPU/GPU budget next to local LLM |
| `README.md` | — | ✅ index of this folder |

## How to use
- `ui_layout.md` is the source of truth for region geometry used by
  `calibrate.py` and `perception/`. If UI changes, update it, then re-run
  calibration.
- `hw_budget.md` defines the 20 Hz capture budget and what's feasible on the
  bot's (low-spec, CPU-only) hardware vs. the orchestrator's (RTX 4090).

## Agent notes
- **Bot hardware is low-spec / CPU-only.** The RTX 4090 is orchestrator-only
  (for running agents) and NOT available to the bot. Keep proposals in
  `hw_budget.md` within the CPU-only budget.
- Cite sources in research docs (sw_research/hw_research convention).
