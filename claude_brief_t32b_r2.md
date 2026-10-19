You are the coder for the MOONLIGHT Tibia bot repo at C:\Users\kamil\moonlight.
This is task **T32b-r2** — batch 2 of 3 of the T32b wiring+config fixes from the T31
senior review. Batch r1 (wiring + cavebot + F-01/F-25) is DONE and committed; the
suite should be green at your start — verify and record the baseline. Do not start
r3's items (F-20, F-27).

## Scope (all of it, in this order)
1. **F-09 (major, S) — unreachable gates become honest gates:** `BotConfig.validate`
   refuses to enable looting without a monster-name perception source and refill
   without a capacity perception source (clear error message naming what's missing).
   The capacity reader itself is M-L and deferred to T34 — the guard is what ships.
2. **F-10 (major, S) — buffs:** fix the default spell list (`exori *` are attacks,
   `utemo` is not real — remove), default OFF, and `validate` forbids enabling buffs
   without buff-icon perception (clear error).
3. **F-11 (major, S) — profile completeness:** default profile becomes
   `moonlight_user` (or `run` requires one — pick one, document it); the check command
   reports missing calibration regions PER ENABLED MODULE (module → OK/MISSING list)
   instead of a generic failure. The actual calibration session is human work (T33
   HIL) — you ship the reporting, not the pixels.
4. **F-13 (major, M) — GUI Start bot:** the GUI must build the loop through
   `cli.build_loop` with real capture/focus (not a bare `GameLoop` with no perceive);
   missing Arduino requires an explicit "simulation" toggle (no silent FakeInput);
   delete `PerceptionWriter`; handle backend re-open on loop restart (T32a note: the
   CLI `finally` calls `backend.close()`, so a restarted loop needs a reopened
   backend); document in the GUI that **T33 uses the CLI**. `tests/unit/test_ui.py`
   smoke must keep passing (headless-safe).

## Out of scope (batch r3)
F-20 (backend rate limit), F-27 (repo hygiene). All perf items (P1-P7).

## Definition of done
- Suite: 0 failures, no regressions vs your recorded start baseline (may grow).
- `ruff check .` clean; `mypy .` clean.
- The validate guards fire with clear messages (looting/refill/buffs refusal errors);
  `moonlight run --help` exits 0.
- GUI: Start uses build_loop, simulation toggle exists, `PerceptionWriter` is gone,
  test_ui smoke passes.
- Update `tests/instructions.md` baseline counts if the test set changed.

## DONE signal
Last line of your final message, exactly:
`T32b-r2 COMPLETE: <one-line summary>`
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
