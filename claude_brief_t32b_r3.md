You are the coder for the MOONLIGHT Tibia bot repo at C:\Users\kamil\moonlight.
This is task **T32b-r3** — batch 3 of 3 (final) of the T32b wiring+config fixes from
the T31 senior review. Batches r1 (wiring+cavebot+F-01/F-25) and r2 (F-09/F-10/F-11/
F-13) are DONE and committed; the suite should be green at your start — verify and
record the baseline.

## Scope (all of it, in this order)
1. **F-20 (major, S-M) — rate-limit at the backend:** count ACTUAL serial commands.
   One "action" via `_send(..., rate_limited=False)` can issue many serial commands
   (clicker iterations, hotkey+click). Keep the 10/s invariant true FOR THE WIRE, and
   update safety test 9 (which T32a changed to a 12/s heal scenario) to match.
2. **F-27 (minor, XS-S) — repo hygiene, all from the findings doc:** add
   `customtkinter` to `pyproject.toml` deps; document the real interpreter (system
   `python` 3.14.7, NO `.venv`) wherever docs reference `.venv/Scripts/python.exe`;
   refresh `docs/perf_profile.md` (it wrongly claims perception on its own thread and
   a 1280×960 capture); delete the stray root-level dirs `__pycache__/`, `decision/`,
   `perception/`, `gui/` (the real packages live under `moonlight/` — verify each
   stray dir is truly stray before deleting); catch `CalibrationError` in `cli.main`
   (cli.py:342) → friendly one-line error, no traceback.

## Out of scope
All perf items (P1-P7 — that's T32c). Full name resolver (T34).

## Definition of done
- Suite: 0 failures, no regressions vs your recorded start baseline (may grow).
- `ruff check .` clean; `mypy .` clean.
- 10/s wire invariant holds (safety test 9 updated and passing).
- Stray dirs gone; `customtkinter` in deps; `CalibrationError` is friendly in the CLI
  (no traceback); `docs/perf_profile.md` and interpreter docs are accurate.
- Update `tests/instructions.md` baseline counts if the test set changed.

## DONE signal
Last line of your final message, exactly:
`T32b-r3 COMPLETE: <one-line summary>`
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
