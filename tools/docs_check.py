#!/usr/bin/env python3
"""Check the PR docs, task files and ``doc:`` links (RULESET.md R2, R3, R9, R10).

Usage:
    python tools/docs_check.py                    # links, task files, graphs
    python tools/docs_check.py --write            # regenerate graphs, then check
    python tools/docs_check.py scope <task.md>    # uncommitted changes vs its writes
    python tools/docs_check.py scope --last-commit

Exits 1 and prints one line per problem. Formats: docs/ROUTER.md.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRS = REPO / "docs" / "prs"
TASKS = REPO / "docs" / "tasks"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", ".ruff_cache", ".mypy_cache",
             ".pytest_cache", "__pycache__", "worktrees"}
STATUSES = {"todo", "doing", "done", "later"}
REQUIRED = ("id", "pr", "status", "needs", "model", "effort", "ran_with", "reads", "writes")
DOC_LINK = re.compile(r"\bdoc:\s+([\w./-]+\.md)(?:#([\w-]+))?")
MD_LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#([\w-]+))?\)")
TASK_ID = re.compile(r"^([A-Z]\d*(?:\.[A-Z]\d+)+):")
GRAPH = re.compile(r"<!-- graph:start -->.*?<!-- graph:end -->", re.S)


def slug(heading: str) -> str:
    """GitHub's heading anchor."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def headings(md: Path) -> dict[str, str]:
    text = md.read_text(encoding="utf-8")
    return {slug(h): h.strip() for h in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)}


def front_matter(text: str) -> dict[str, str | list[str]]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    out: dict[str, str | list[str]] = {}
    for line in m.group(1).splitlines():
        key, _, val = line.partition(":")
        val = val.split(" #")[0].strip()
        if val.startswith("["):
            out[key.strip()] = [v.strip() for v in val[1:-1].split(",") if v.strip()]
        else:
            out[key.strip()] = val
    return out


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


def doc_links() -> list[tuple[Path, str, str | None]]:
    """Every ``doc:`` link in the code: (source file, target path, anchor)."""
    return [(py, t, a) for py in files("**/*.py")
            for t, a in DOC_LINK.findall(py.read_text(encoding="utf-8")) if t]


def load_tasks() -> dict[str, tuple[Path, dict[str, str | list[str]], str]]:
    out = {}
    for md in files("docs/tasks/*/*.md"):
        text = md.read_text(encoding="utf-8")
        fm = front_matter(text)
        out[str(fm.get("id", md.stem))] = (md, fm, text)
    return out


def check() -> list[str]:
    problems = []
    for src, target, anchor in doc_links():
        problems.append(check_link(src, target, anchor or None, REPO) or "")
    for md in [*files("RULESET.md"), *files("docs/prs/*.md"), *files("docs/tasks/*/*.md")]:
        for target, anchor in MD_LINK.findall(md.read_text(encoding="utf-8")):
            if not target.startswith("http"):
                problems.append(check_link(md, target, anchor or None, md.parent) or "")

    tasks = load_tasks()
    for tid, (md, fm, text) in tasks.items():
        where = md.relative_to(REPO)
        missing = [k for k in REQUIRED if k not in fm]
        if missing:
            problems.append(f"{where}: front matter misses {', '.join(missing)}")
            continue
        if fm["status"] not in STATUSES:
            problems.append(f"{where}: status {fm['status']!r} not in {sorted(STATUSES)}")
        if fm["pr"] != md.parent.name or not (PRS / f"{fm['pr']}.md").is_file():
            problems.append(f"{where}: pr {fm['pr']!r} must match its folder and a docs/prs file")
        problems += [f"{where}: needs unknown task {n}" for n in fm["needs"] if n not in tasks]
        for ref in fm["reads"]:
            target, _, anchor = str(ref).partition("#")
            if target.endswith(".md"):
                problems.append(check_link(md, target, anchor or None, REPO) or "")
        changed = text.split("## What changed and why", 1)[-1].strip()
        if fm["status"] == "done" and (not changed or not fm["ran_with"]):
            problems.append(f"{where}: done but What changed and why or ran_with is empty")

    for pr in files("docs/prs/*.md"):
        text = pr.read_text(encoding="utf-8")
        if not GRAPH.search(text):
            problems.append(f"{pr.relative_to(REPO)}: no graph block")
        elif GRAPH.search(text).group(0) != graph_block(pr, tasks):  # type: ignore[union-attr]
            problems.append(f"{pr.relative_to(REPO)}: graph is stale, run --write")
    return [p for p in problems if p]


def graph_block(pr: Path, tasks: dict[str, tuple[Path, dict[str, str | list[str]], str]]) -> str:
    """Mermaid graph: task -> task (needs), task -> file (writes), file -> concept (doc:)."""
    mine = {tid: fm for tid, (_, fm, _) in sorted(tasks.items()) if fm.get("pr") == pr.stem}
    titles = headings(pr)
    concepts = sorted({(py.relative_to(REPO).as_posix(), a) for py, t, a in doc_links()
                       if a and (REPO / t).resolve() == pr.resolve()})
    paths = sorted({str(w) for fm in mine.values() for w in fm.get("writes", [])}
                   | {p for p, _ in concepts})
    tid_node = {tid: f"t{i}" for i, tid in enumerate(mine)}
    file_node = {p: f"f{i}" for i, p in enumerate(paths)}
    concept_node = {a: f"c{i}" for i, a in enumerate(sorted({a for _, a in concepts}))}

    lines = ["```mermaid", "flowchart LR",
             "  classDef done fill:#2e7d32,color:#fff",
             "  classDef doing fill:#f9a825,color:#000",
             "  classDef later fill:#9e9e9e,color:#fff"]
    for tid, fm in mine.items():
        status = fm.get("status")
        lines.append(f'  {tid_node[tid]}["{tid}"]' + (f":::{status}" if status != "todo" else ""))
    lines += [f'  {n}["{p}"]' for p, n in file_node.items()]
    lines += [f'  {n}(["{titles.get(a, a)}"])' for a, n in concept_node.items()]
    for tid, fm in mine.items():
        lines += [f"  {tid_node[n]} --> {tid_node[tid]}" for n in fm.get("needs", [])
                  if n in tid_node]
        lines += [f"  {tid_node[tid]} -.-> {file_node[str(w)]}" for w in fm.get("writes", [])]
    lines += [f"  {file_node[p]} --- {concept_node[a]}" for p, a in concepts]
    lines.append("```")
    return "<!-- graph:start -->\n" + "\n".join(lines) + "\n<!-- graph:end -->"


def write_graphs() -> None:
    tasks = load_tasks()
    for pr in files("docs/prs/*.md"):
        text = pr.read_text(encoding="utf-8")
        new = GRAPH.sub(lambda _: graph_block(pr, tasks), text)
        if new != text:
            pr.write_text(new, encoding="utf-8")


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line]


def scope(task: Path | None, last_commit: bool) -> list[str]:
    """Files changed outside the task's writes (R2)."""
    if last_commit:
        m = TASK_ID.match(git("log", "-1", "--format=%s")[0])
        found = [md for md, _, _ in load_tasks().values() if m and md.stem == m.group(1)]
        if not found:
            print("last commit is not a task commit, nothing to check")
            return []
        task, changed = found[0], git("diff", "--name-only", "HEAD~1", "HEAD")
    else:
        assert task is not None
        changed = git("diff", "--name-only", "HEAD") + git("ls-files", "--others",
                                                           "--exclude-standard")
    task = task.resolve()
    fm = front_matter(task.read_text(encoding="utf-8"))
    allowed = [str(w) for w in fm["writes"]]
    allowed += [task.relative_to(REPO).as_posix(), f"docs/prs/{fm['pr']}.md"]
    allowed += [(Path(str(w)).parent / "README.md").as_posix() for w in fm["writes"]]
    return [f"{path}: changed but not in {task.name} writes" for path in changed
            if not any(fnmatch.fnmatch(path, pat) for pat in allowed)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cmd", nargs="?", choices=["scope"])
    parser.add_argument("task", nargs="?", type=Path)
    parser.add_argument("--write", action="store_true", help="regenerate PR graphs first")
    parser.add_argument("--last-commit", action="store_true", help="scope: check HEAD's task")
    args = parser.parse_args()

    if args.cmd == "scope":
        if not args.task and not args.last_commit:
            parser.error("scope needs a task file or --last-commit")
        problems = scope(args.task, args.last_commit)
    else:
        if args.write:
            write_graphs()
        problems = check()
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
