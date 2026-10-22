# .github/workflows/

GitHub Actions CI. `ci.yml` has two jobs on Python 3.12:

1. `test` (blocking): `ruff check .`, `mypy --strict .`, `pytest -v`.
2. `benchmark` (non-blocking, `continue-on-error`): `pytest --benchmark-only`, uploads `benchmark_results.json` as an artifact.

Both install with `pip install -e ".[dev]"`.

## How to test locally (mirrors CI)

```bash
python -m ruff check .
python -m mypy --strict .
python -m pytest -v
```

## Do not

- Keep CI and local checks in sync (same commands, same Python version).
- Never let the benchmark job block the build.
