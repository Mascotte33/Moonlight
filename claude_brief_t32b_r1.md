You are the coder for the MOONLIGHT Tibia bot repo at C:\Users\kamil\moonlight.
This is task **T32b-r1** — batch 1 of 3 of the T32b wiring+config fixes from the T31
senior review. Batches r2/r3 follow after you; do not start their items.

## Where the previous run died (CRITICAL — start here)
A previous agent was interrupted mid-refactor by an account session limit. Its WIP is
COMMITTED as `d518a93` (see `git show d518a93 --stat`):
- `moonlight/decision/cavebot.py` — F-03/F-06 refactor done IN THE MODULE:
  `Cavebot.__init__` now takes `minimap_region: Rect` (was `MinimapReader` +
  `FloorReader` + `frame_provider`), reads position from `WorldState.position`
  (perception owns the single tracker), and gained a `resume()` method
  (stuck-timer reset when MOVE is re-entered).
- `moonlight/decision/targeting.py`, `moonlight/loop.py` — related partial changes.
The suite is currently RED (3 failures): `test_headless_smoke_all_modules`
(TypeError — `cli.build_loop` still wires the OLD Cavebot signature) and safety
tests 4/5 (cavebot stuck/lost).
**Your first job is to finish this refactor, not to review it from scratch.**
If part of the WIP goes in a wrong direction, fix it in place; do not revert.

## Scope (all of it, in this order)
1. **Finish the interrupted wiring (F-03, unblocks everything):** rewire
   `cli.build_loop` (and any remaining callers) to the new `Cavebot` signature —
   minimap region from config, position from the perception-owned tracker. Fix the
   3 failing tests. Suite → 0 failures.
2. **F-03 (findings doc, critical, M) — finish:** perception owns the tracker;
   `MinimapReader` is built with an absolute start position seeded from a new config
   field (and/or CLI arg) instead of "offset from wherever the bot started". Add a
   replay test asserting absolute positions on one labelled golden session (labels
   exist in `tests/golden/`).
3. **F-06 (major, S) — verify + wire:** the `resume()` stuck-timer reset must actually
   be called when MOVE is (re)entered, so a >5 s fight doesn't trigger a false stuck
   halt. Test it.
4. **F-01 interim (critical, XS) — no auto-attack with unknown names:** with no name
   resolver wired, every battle entry is a nameless "monster", so targeting must treat
   unknown entries as *unknown → do not auto-attack*. Enforce in `BotConfig.validate`
   / `build_loop`: targeting enabled without a resolver is an explicit warning at
   start (documented, visible), never a silent bypass. Player detection stays 0 until
   a resolver exists (T34, human calibration + OCR — not this job).
5. **F-25 (minor, S) — cavebot `start(nearest=True)`:** after refill/restart, resume
   from the nearest waypoint, not waypoint 0.

## Out of scope (batch r2/r3 — note them in your DONE message if you trip over them)
F-09, F-10, F-11, F-13, F-20, F-27. All perf items (P1-P7). Full name resolver (T34).

## Definition of done
- Suite: 0 failures (was 3 at your start; may grow with your new tests), no regressions.
- `ruff check .` clean; `mypy .` clean.
- `moonlight run --help` exits 0; the no-resolver targeting warning fires visibly.
- Replay test for absolute minimap positions passes.
- Update `tests/instructions.md` baseline counts if the test set changed.

## DONE signal
Last line of your final message, exactly:
`T32b-r1 COMPLETE: <one-line summary>`
## Read first (source of truth)
- `docs/senior_review_findings.md` — each item below has file:line refs, Description,
  Fix, and a "T32 scope" line. Read it fully before coding.
- `PROGRESS.md`, `instructions.md` (root + inside `moonlight/`, `tests/`, `gui/`).
- The specific source files named in your scope below, plus `moonlight/cli.py`,
  `moonlight/config.py`, `moonlight/profile.py`.

## Conventions (non-negotiable)
- Python 3.11+ style, type hints everywhere; no `print()` in production code
  (logging only).
- `ruff check .` and `mypy .` MUST stay clean.
- Tests in `tests/` subdirs. Use system `python` (3.14.7) — there is NO `.venv`.
- Do NOT commit. The orchestrator verifies and commits after you.
- At the START of your run: run `python -m pytest -q`, record the pass/fail count as
  your baseline. You must end with 0 failures and no regressions vs baseline (count
  may grow with new tests).
- Budget: 20-45 minutes. If you run low on context, stop and report done vs remaining.
