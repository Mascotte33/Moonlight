# instructions.md — `.github/workflows/` (CI)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14.

## What this is
GitHub Actions CI. Two jobs:
1. **test** (blocking): ruff + mypy --strict + pytest.
2. **benchmark** (non-blocking, `continue-on-error`): runs `--benchmark-only`,
   uploads results as an artifact.

## Status
| File | Task | Status |
|------|------|--------|
| `ci.yml` | T04 | ✅ DONE (69 lines) — created by prior session |

## ⚠️ Known concern
CI does `pip install -e ".[dev]"`, but `pyproject.toml` uses
`[tool.setuptools.packages.find] where=["."] include=["moonlight*"]`, which
expects a nested `moonlight/` package dir. The repo currently uses a **flat**
layout (`__init__.py` at repo root), so the editable install may find no
package. **Verify `pip install -e .` actually works** before trusting CI green.
This is part of the two-session structural collision — see root `instructions.md`.

## How to test locally (mirrors CI)
```bash
cd <repo-root>
python -m ruff check .
python -m mypy --strict .
python -m pytest -v
```

## Agent notes
- Keep CI and local checks in sync (same commands, same Python 3.12).
- Benchmark job is informational only — never let it block the build.
