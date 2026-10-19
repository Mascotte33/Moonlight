You are the coder for the MOONLIGHT Tibia bot repo at C:\Users\kamil\moonlight.
This is task **T32a — loop-safety fixes** from the T31 senior review.

## Read first (source of truth)
- `docs/senior_review_findings.md` — the findings doc. Every item below has file:line
  refs, a Description, a Fix, and a "T32 scope" line there. Read it fully before coding.
- `PROGRESS.md` (status + conventions), `instructions.md` (repo root), and the
  `instructions.md` files inside `moonlight/` and `tests/` if present.
- `moonlight/loop.py`, `moonlight/state.py`, `moonlight/input.py`, `moonlight/cli.py`,
  `tests/safety/test_safety_suite.py`, and wherever the existing loop tests live.

## Conventions (non-negotiable)
- Python 3.11+ style, type hints everywhere; no `print()` in production code (logging only).
- `ruff check .` and `mypy .` MUST stay clean (89 files at baseline).
- Tests live in `tests/` subdirs. Run the FULL suite: `python -m pytest -q`
  (baseline: 843 passed, 8 skipped). Use system `python` (3.14.7) — there is NO `.venv`
  in this repo.
- Do NOT commit. The orchestrator commits after verifying.
- Estimated budget: 15–40 minutes of work. If you run low on context, stop and report
  exactly which items are done vs remaining.

## Scope — fix exactly these 11 findings
1. **F-02 (critical)** — every stop path releases inputs: `_act_kill` and `_shutdown`
   call `self.input.release_all()` DIRECTLY (ungated by focus/`screen_ok`; exceptions
   swallowed + logged). Stop the Arduino heartbeat thread when the loop stops. The CLI
   `finally` block must `close()` the backend.
2. **F-04 (major)** — guard `tick()` inside `run()` with try/except → log,
   `release_all`, then stop (or pause per F-05). No exception may end the run with
   inputs held.
3. **F-05 (major)** — unfocused / transient perception error = **PAUSE, not permanent
   stop**: new loop state PAUSED — release inputs, keep the loop ticking as cheap no-ops,
   resume automatically when perceive is healthy again; STOP only after N consecutive
   failures or > X seconds (choose N and X, document them in the code). Log both
   transitions.
4. **F-07 (major)** — press `attack_combo` only when `snap.selected_target is None` or
   the selected target changed, with a ≥ 1 s re-press cooldown.
5. **F-08 (major)** — default `max_monsters_to_engage` to 4; "too many monsters" becomes
   a logged warning that keeps attacking (no FLEE→ALARM dead-end unless a real flee
   route exists — document that real flee is later work).
6. **F-12 (major)** — operator alerts: the loop surfaces module alarms
   (`cavebot.alarm`, `refill.alarm`) as ALARM state + one alert sink: winsound beep
   (guarded import, must never raise) + a log line. PM detection: wire it only if a
   clean wiring exists; otherwise document it as absent.
7. **F-26 (minor)** — F12 monitor: use `state & 0x8000` (currently down), not `0x0001`.
8. **F-23 (minor)** — `state.last_update` written with `time.monotonic()`.
9. **F-24 (minor)** — single source of truth for heal thresholds: the loop derives
   heal-related states from the healing module (remove the duplicated 30/60 vs 30/70
   constants).
10. **F-22 (minor)** — heals get priority over the shared 10/s rate token (separate
    bucket or priority reservation — keep it simple); `_act_loot`'s step must return
    `looting.step(snap)` so a no-op loot step doesn't burn a token.
11. **F-19 (major, tests)** — extend `tests/safety/test_safety_suite.py` with a
    build_loop-based section: build the loop through the same production wiring the CLI
    uses (`cli.build_loop` / `make_perceive` / focus check) with `FakeInput`, golden
    frames and a toggleable focus check. New invariants: kill while unfocused releases;
    `!screen_ok` stop releases; exception in perceive/module releases and stops or
    pauses; pause on unfocus resumes on refocus. All existing 12 invariants must keep
    passing.

## Out of scope (T32b/T32c — do NOT fix, just note if you trip over them)
F-01, F-03, F-06, F-09, F-10, F-11, F-13, F-14, F-15, F-16, F-17, F-18, F-20, F-21,
F-25, F-27, and the P1-P7 perf fixes. If a fix in your scope reveals a bug in a
perception module or another out-of-scope area, do NOT fix it — report it in your
DONE message.

## Definition of done
- All 11 fixes implemented.
- `python -m pytest -q` → 0 failures (pass count may grow with the new tests).
- `ruff check .` clean; `mypy .` clean.
- The new build_loop safety invariants pass.
- Changes confined to: `moonlight/loop.py`, `moonlight/state.py`, `moonlight/input.py`,
  `moonlight/cli.py`, `moonlight/decision/targeting.py`, `moonlight/decision/healing.py`,
  `moonlight/decision/alarms.py` (only if needed for F-12), `tests/safety/`, and existing
  loop test files. Update any test-count numbers in `tests/instructions.md` if you add
  tests there.

## DONE signal
Last line of your final message, exactly:
`T32a COMPLETE: <one-line summary>`
