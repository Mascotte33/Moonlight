You are the coder for the MOONLIGHT Tibia bot repo at C:\Users\kamil\moonlight.
This is task **T32c — performance + data-quality fixes** from the T31 senior review
(`docs/senior_review_findings.md`). It is the third and last of the T32 split.

## Read first
- `docs/senior_review_findings.md` — the "## Performance" section (measured-facts table
  + P1-P7 budget fixes) and the "## Data / label quality" section (F-16, F-17, F-21),
  plus F-14 (clicker) and F-15 (timing test) under Performance, F-18 under
  "## Test coverage".
- `PROGRESS.md` — T32a/T32b status. NOTE: T32b may have landed while you start —
  check `git log` first. After T32b, `Cavebot` reads position from
  `WorldState.position` (no own capture) — P1's cavebot half may then be moot;
  verify against the actual code before changing anything.

## Context (what exists now)
- T32a (committed): loop safety — PAUSED state, tick error guard, release_all on all
  stop paths, attack de-spam, beep alerts, 857 pass baseline at that point.
- T32b (committed or in flight): wiring/config — no-resolver no-attack, single
  absolute-position tracker (cavebot refactor), GUI via build_loop, backend rate
  limit, repo hygiene.
- Measured (T30, on this machine): production tick perception (vitals+battle+minimap+
  floor) < 7 ms p95 — comfortably in budget. The real risks are the UNMEASURED
  full-window capture at 2560×1369 (~15-30 ms, repeated per consumer), the clicker
  doing up to 6 captures per tick, and the serial ACK wait (up to 0.5 s).

## Scope (do ALL, in this order)
1. **P1** — one capture per tick: frame cache keyed by tick, shared with every
   consumer that needs a frame (`cli.py`, `clicker.py`, and cavebot if it still
   captures post-T32b).
2. **P2** — `capture.py:get_frame`: capture the union of calibrated regions (or
   per-region grabs) instead of the full window; keep full-frame grab as a fallback
   for uncalibrated regions.
3. **P3** — `perception/sprites.py`: on-demand only (no battle-list confirmation),
   2× downscaled ROI around the player, max N templates.
4. **P4** — `perception/logocr.py`: off-tick worker ≤ 1 Hz (daemon thread), single
   `image_to_data` call per read (text + conf from one run), drop the 2× INTER_CUBIC
   upscale (or INTER_LINEAR on a threshold image), only the last 2-3 log lines.
   **F-21**: log once on first failure with the exception; dedupe by (line,
   position), not text; expose a health check usable by `moonlight check`.
5. **P5 + F-14** — `clicker.py`: per-tick state machine (one move + one capture per
   tick), 15-30 ms settle delay between move and re-capture, capture only a small ROI
   around the target. Cursor visibility is an HIL question (T33) — structure the code
   so open-loop absolute positioning can be switched in by config if HIL shows the
   cursor is invisible in captures.
6. **P6** — `input.py`: fire-and-forget keys must not block the tick on the serial
   ACK (shorter timeout + async ACK check, or queue with non-blocking drain).
7. **F-15 + P7** — `tests/perf/test_timing_report.py`: measure the REAL
   `make_perceive` + `ScreenCapture.get_frame` at native resolution, include a
   cavebot step with a real frame provider, assert p95 < 50 ms; skip cleanly on CI
   without a display.
8. **F-16** — battle-list region: recalibrate config to full list height (read the
   existing region from `config/profiles/` — update the region, don't guess pixels
   you can't see); read the HP bar fill per row. Golden labelling of
   `monster_count`/`selected_target` is a HUMAN step (T33) — just make sure the
   reader emits the fields.
9. **F-17** — minimap marker robustness: validate on radar-frame/shift response
   instead of the centre cross; continue tracking through occlusion (phase
   correlation doesn't need the cross); on rejected shift return None/flag and
   re-anchor. Add unit tests with synthetic shifts (known shift → detected;
   implausible → rejected → re-anchor).
10. **F-18 (code side)** — golden loader: make it fail loudly when labels are
    placeholders (detect the placeholder sentinel and refuse to run the golden gate
    with a clear message listing the unlabeled modules); new unit test. The actual
    labelling happens in T33 (human).

## Conventions
- Python 3.11+ style; type hints everywhere; no `print()` in production code
  (GUI/CLI/logging only); tests in `tests/` subdirs.
- `ruff check .` and `mypy .` must stay clean (89 files).
- Full `python -m pytest -q` must stay green — record the baseline count at the
  start of your run (it reflects T32a+T32b) and never regress it.
- System Python 3.14.7 (there is NO `.venv` on this box).
- Do NOT commit — the orchestrator commits after verification.
- If a fix needs a calibrated region you cannot see (no display), keep the current
  value, add a `# T33: recalibrate on server` comment, and note it in your report —
  do not invent pixels.

## Acceptance (report at the end, verbatim)
- Baseline pytest count at start → final count (pass/skip), ruff + mypy status.
- Per item 1-10: one line — what changed, file(s), and for P1-P6/F-15 the expected
  saving from the findings doc.
- Anything deferred to T33 (HIL) with the exact reason.
- First line of your final message: `T32C STATUS: DONE` or `T32C STATUS: BLOCKED — <why>`.
