# Ruleset

Every agent follows these rules on every task. A PR doc may add rules of its
own under **PR rules**. When the two disagree, this file wins.

Each rule has an ID that is never reused. To remove a rule, delete its entry.
To change a rule, edit it in place and keep its ID. Cite rules by ID
(`R4`) in task files, PR docs and commit messages.

## Where things are

- `docs/prs/<slug>.md` is one PR: goal, data shapes, concepts, task list, owner checks.
- `docs/tasks/<slug>/<id>.md` is one task of that PR, with its brief for the agent.
- `docs/ROUTER.md` is how the session running a PR picks and hands out tasks.
- Each folder's `README.md` says what the folder holds.
- `# doc: <path>#<anchor>` comments in code link to the concept that explains it.

## Rules

**R1. Read only what the task names.**
Read this file, your task file and the entries in its `reads`. A quick grep to
find callers is fine. Do not read whole plans or unrelated folders.
Why: every extra file costs context and pulls the agent off the task.

**R2. Write only the files in `writes`.**
If the task needs another file, stop and report which file and why. Only the
router or the owner edits `writes`, and that edit goes in the task's commit.
`python tools/docs_check.py scope` enforces this.
Why: a reviewer must be able to trust that the diff stays inside the task.

**R3. One task is one commit, with its docs.**
The subject starts with the task ID (`C2.F3.K1: Window size in the profile`).
The same commit sets the task's `status` and `ran_with`, fills its
**What changed and why**, and regenerates the PR graph (R10). Status lives
only in the task file's front matter.
Why: the code and its explanation are reviewed together, and the docs never lag.

**R4. Keep the checks green.**
`pytest`, `ruff check .` and `mypy moonlight tests` pass before every commit.
A delegated agent runs its own new tests and `ruff check`. The router runs
the full suite once before committing.
Why: running the full suite twice per task wastes minutes.

**R5. Stop and ask the owner instead of working around a problem.**
Ask when a reference image or golden screenshot is missing, when the task's
**Done when** can't be met as written, or when R2 blocks you.
Why: a workaround hides the problem from the person who can fix it.

**R6. No new dependencies.**
Use the standard library and what `pyproject.toml` already lists.
Why: every dependency is code the owner has to install and trust.

**R7. Tests that need local-only data skip when it is missing.**
`tests/golden/*.png` and `data/templates/anchors/` are gitignored and live
only on the owner's PC. Use
`pytest.mark.skipif(not any(GOLDEN.glob("*.png")), reason="golden screenshots not present")`.
Why: the suite must pass on any machine.

**R8. Test the logic, not the widgets.**
No test opens a window. Visual checks are **Owner checks** and are never
claimed as tested.
Why: GUI tests are slow and flaky, and only a person can judge the look.

**R9. Link code to its concept.**
Code that implements a concept from a PR doc carries
`# doc: docs/prs/<slug>.md#<anchor>` on the module docstring or the
definition. Link to concepts, never to task files. Add the link when you
touch older code that has none.
Why: the next agent finds the explanation by grepping `doc:`.

**R10. Don't edit generated blocks by hand.**
The graph between `<!-- graph:start -->` and `<!-- graph:end -->` in a PR doc
is generated. Run `python tools/docs_check.py --write` after changing task
front matter or `doc:` links.
Why: a hand-edited graph drifts, and the check fails on drift.

**R11. Commit and keep going.**
Commit each finished task and start the next one without asking. Decide
design calls yourself and note them in the commit. Ask the owner only when a
whole PR or phase is done, for its owner checks and PR approval.
Why: the owner said so. They are not needed between tasks.

**R12. Report only what the owner must check.**
A report to the owner lists the owner checks and how to open the app, with
the test results on one line. Every open item names who handles it and when
(a later task, or "you decide").
Why: long changed/expect/commands write-ups went unread.

**R13. Size the model to the task.**
Use the cheapest model family that reliably finishes the task. Tiny changes
(under about 50 lines) are done inline without an agent. The router states
each choice as `{"selected_model": "<id>", "complexity_score": N, "justification": "<one sentence>"}`.
Why: the owner said not to always use the best model.

**R14. One doc per folder.**
A folder's `README.md` is its only doc. When you add, remove or finish a file,
update that README's table in the same commit.
Why: two docs per folder drifted apart and contradicted each other.
