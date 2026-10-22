# Router

The router is the session that runs a PR. It picks tasks, hands each one to
an agent (or does it inline), checks the result and commits. It follows
`RULESET.md` like every agent.

## The loop

1. **Pick.** Open `docs/prs/<slug>.md`. Walk its Tasks list in order and take
   the first task whose `status` is `todo` and whose `needs` are all `done`.
   Skip `later` tasks. The graph shows statuses at a glance.
2. **Size.** Read the task's `model` and `effort`. Spawn with the family alias
   (`haiku`, `sonnet`, `opus`, `fable`). The Agent tool can't pin a
   generation, so whatever runs is the family's current model. State the
   choice per R13. Tiny tasks are done inline.
3. **Brief.** Write only what the task file can't know: what earlier tasks
   actually changed, surprises found mid-PR, owner decisions made in chat.
   Never restate the task. Append the brief to the task's **Router notes**
   with the date.
4. **Spawn.** The agent's prompt is `RULESET.md`, the task file and your
   brief, nothing else. The agent reads the task's `reads` itself.
5. **Check.** Run `python tools/docs_check.py scope docs/tasks/<slug>/<id>.md`.
   A file outside `writes` means: revert it, or widen `writes` yourself if the
   owner agreed. Then run the full checks (R4).
6. **Close.** Set `status: done` and `ran_with: <model id>`, make sure
   **What changed and why** is filled, run
   `python tools/docs_check.py --write`, commit (R3). Go back to 1.

When no task is left, open the PR with the owner checks from every task and
ask the owner for tests and approval (R11).

## Task file

Path: `docs/tasks/<slug>/<id>.md`, where `<slug>` is the PR doc's file name.

```markdown
---
id: C2.F3.K1
pr: gui-calibration
status: todo            # todo | doing | done | later
needs: [C2.F2.K1]
model: claude-sonnet-5-5
effort: medium          # low | medium | high
ran_with:               # filled when done
reads: [moonlight/setup/calibrate.py, docs/prs/gui-calibration.md#data-shapes]
writes: [moonlight/setup/calibrate.py, tests/unit/test_calibrate.py]
---

# C2.F3.K1: Window size and UI scale in the profile

Part of [gui-calibration](../../prs/gui-calibration.md).

## Brief

What to build, written to the agent. Self-contained: the agent sees only
RULESET.md, this file and its `reads`.

**Don't touch:** ...

**Done when:** ...

**Owner checks:** ... (only if a person must look at something)

## Router notes

## What changed and why
```

Front matter values are a single word or an inline list `[a, b]`.
`writes` entries may use globs (`data/templates/anchors/*.png`).
A ` #` (space, then hash) starts a comment. A `#` inside a path is an anchor.

## PR doc

Path: `docs/prs/<slug>.md`. Sections in this order: Goal, How it works today,
Data shapes, PR rules, Graph, Concepts (one `###` section per concept, the
anchors that `doc:` comments link to), Tasks, Owner checks.

The Graph section holds only the generated block:

```markdown
<!-- graph:start -->
<!-- graph:end -->
```
