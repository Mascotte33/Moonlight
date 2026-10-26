"""tools/docs_check.py: the repo's docs pass, and broken docs are caught (RULESET.md R2-R10)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "docs_check.py"
NO_DOCS = pytest.mark.skipif(not (REPO / "docs").is_dir(), reason="docs/ is owner-local")
TAG = "doc:"  # kept out of the literals below, or the checker would scan them as links


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("docs_check", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=REPO,
                          capture_output=True, text=True)


@NO_DOCS
def test_repo_docs_pass() -> None:
    result = run()
    assert result.returncode == 0, result.stdout


@NO_DOCS
def test_last_task_commit_stays_in_its_writes() -> None:
    has_parent = subprocess.run(["git", "rev-parse", "--verify", "HEAD~1"], cwd=REPO,
                                capture_output=True).returncode == 0
    if not has_parent:
        pytest.skip("shallow checkout, no parent commit")
    result = run("scope", "--last-commit")
    assert result.returncode == 0, result.stdout


def test_broken_docs_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checker = load_checker()
    monkeypatch.setattr(checker, "REPO", tmp_path)
    monkeypatch.setattr(checker, "PRS", tmp_path / "docs" / "prs")
    (tmp_path / "docs" / "prs").mkdir(parents=True)
    (tmp_path / "docs" / "tasks" / "demo").mkdir(parents=True)
    (tmp_path / "docs" / "prs" / "demo.md").write_text(
        "# Demo\n\n### Real concept\n\n<!-- graph:start -->\n<!-- graph:end -->\n",
        encoding="utf-8")
    (tmp_path / "docs" / "tasks" / "demo" / "A1.F1.K1.md").write_text(
        "---\nid: A1.F1.K1\npr: demo\nstatus: done\nneeds: [A1.F1.K9]\nmodel: m\n"
        "effort: low\nran_with: m\nreads: [docs/prs/demo.md#gone]\nwrites: [x.py]\n---\n\n"
        "## What changed and why\n", encoding="utf-8")
    (tmp_path / "x.py").write_text(f"# {TAG} docs/prs/demo.md#missing-concept\n",
                                   encoding="utf-8")

    problems = "\n".join(checker.check())

    assert "no heading #missing-concept" in problems
    assert "no heading #gone" in problems
    assert "needs unknown task A1.F1.K9" in problems
    assert "done but What changed" in problems
    assert "graph is stale" in problems

    (tmp_path / "x.py").write_text(f"# {TAG} docs/prs/demo.md#real-concept\n", encoding="utf-8")
    checker.write_graphs()
    assert "graph is stale" not in "\n".join(checker.check())
