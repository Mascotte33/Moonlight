"""tools/docs_check.py: the repo's docs pass, and broken docs are caught (RULESET.md R9)."""

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


def test_broken_docs_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checker = load_checker()
    monkeypatch.setattr(checker, "REPO", tmp_path)
    (tmp_path / "docs" / "prs").mkdir(parents=True)
    demo = tmp_path / "docs" / "prs" / "demo.md"
    demo.write_text("# Demo\n\n### Real concept\n\nSee [gone](other.md).\n", encoding="utf-8")
    (tmp_path / "x.py").write_text(f"# {TAG} docs/prs/demo.md#missing-concept\n",
                                   encoding="utf-8")

    problems = "\n".join(checker.check())

    assert "no heading #missing-concept" in problems
    assert "link target other.md does not exist" in problems

    (tmp_path / "x.py").write_text(f"# {TAG} docs/prs/demo.md#real-concept\n", encoding="utf-8")
    demo.write_text("# Demo\n\n### Real concept\n", encoding="utf-8")
    assert checker.check() == []
