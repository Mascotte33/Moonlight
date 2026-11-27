"""Test helper: give a ScriptRunner the loop's modules."""

from __future__ import annotations

from typing import Any


def attach(runner: Any, **modules: Any) -> None:
    """Hand *runner* the loop's modules (as the loop does when it starts)."""
    from moonlight.core.loop import DecisionModules

    m = DecisionModules(**modules)
    runner.modules = lambda: m
    runner.all_modules = lambda: m
