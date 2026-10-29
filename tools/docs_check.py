#!/usr/bin/env python3
"""Check the ``doc:`` links in code and the links in RULESET.md and the PR docs (RULESET.md R9).

Usage:
    python tools/docs_check.py

Exits 1 and prints one line per broken link.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", ".ruff_cache", ".mypy_cache",
             ".pytest_cache", "__pycache__", "worktrees"}
DOC_LINK = re.compile(r"\bdoc:\s+([\w./-]+\.md)(?:#([\w-]+))?")
MD_LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#([\w-]+))?\)")


def slug(heading: str) -> str:
    """GitHub's heading anchor."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def headings(md: Path) -> set[str]:
    return {slug(h) for h in re.findall(r"^#{1,6}\s+(.+)$", md.read_text(encoding="utf-8"), re.M)}


def files(pattern: str) -> list[Path]:
    return sorted(p for p in REPO.glob(pattern)
                  if not SKIP_DIRS.intersection(p.relative_to(REPO).parts))


def check_link(src: Path, target: str, anchor: str | None, base: Path) -> str | None:
    path = (base / target).resolve()
    where = src.relative_to(REPO)
    if not path.is_file():
        return f"{where}: link target {target} does not exist"
    if anchor and anchor not in headings(path):
        return f"{where}: {target} has no heading #{anchor}"
    return None


def check() -> list[str]:
    problems = []
    for py in files("**/*.py"):
        for target, anchor in DOC_LINK.findall(py.read_text(encoding="utf-8")):
            problems.append(check_link(py, target, anchor or None, REPO))
    for md in [*files("RULESET.md"), *files("docs/prs/*.md")]:
        for target, anchor in MD_LINK.findall(md.read_text(encoding="utf-8")):
            if not target.startswith("http"):
                problems.append(check_link(md, target, anchor or None, md.parent))
    return [p for p in problems if p]


def main() -> int:
    problems = check()
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
