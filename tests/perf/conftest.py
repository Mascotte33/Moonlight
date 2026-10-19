"""Golden-suite reporting: per-module accuracy lines printed after the run.

Tests take the ``golden_summary`` fixture and append one line each; pytest
prints them in a "golden accuracy" section at the end of the run (passing
tests' stdout is captured, so ``print`` alone would be lost).
"""

from __future__ import annotations

import pytest

_LINES: list[str] = []


@pytest.fixture
def golden_summary() -> list[str]:
    """Shared list of summary lines for the end-of-run report."""
    return _LINES


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print the collected golden accuracy lines."""
    if _LINES:
        terminalreporter.section("golden accuracy (T28)")
        for line in _LINES:
            terminalreporter.write_line(line)
