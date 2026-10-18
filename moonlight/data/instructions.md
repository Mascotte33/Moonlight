# instructions.md — `data/` (dataset storage)

**Read this before touching anything in this folder.**
Last verified: 2026-10-15.

## What this is
Local data storage. `golden/` holds the T08 golden-frame dataset
(screenshots + `labels.json`) that `tests/perf/` validates `perception/`
against.

## Status
- `golden/` — ⚠️ empty / not created yet; T08 is pending user input.

## Agent notes
- **Never fabricate golden frames or labels.** If no data exists, the
  golden tests skip cleanly — keep it that way.
- Labels are produced by `tools/label_golden.py` (schema documented in
  `tools/instructions.md`). Keep that schema stable.
- Don't commit bulk image data to version control unless the orchestrator
  says otherwise; treat this folder as local working data.
