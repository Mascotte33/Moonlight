"""The scripts folder as the Scripts tab sees it: make, rename and delete
scripts, and keep the routes' ``script`` waypoints in step with them.

# doc: docs/prs/scripts-page.md#scripts-tab
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "NEW_SCRIPT",
    "delete_script",
    "name_error",
    "new_script",
    "rename_script",
    "routes_using",
    "script_names",
]

#: What New writes: a working script with the commands to copy from.
NEW_SCRIPT = '''def run(bot):
    # Each yield is one step. Delete the lines you don't need.
    yield bot.press("f9")           # press a hotkey
    yield 2                         # wait 2 seconds
    if bot.mana > 90:               # bot.hp, bot.mana, bot.cap are percents
        yield bot.say("utevo lux")  # type text in the chat
    if (bot.count("mana") or 0) < 50:  # mana / health potions on the action bar
        yield bot.goto("town")      # jump the route to the label town
'''


def script_names(scripts_dir: Path) -> list[str]:
    """The scripts in *scripts_dir*, sorted (the tab's list and run order)."""
    return sorted(p.stem for p in scripts_dir.glob("*.py"))


def name_error(scripts_dir: Path, name: str) -> str | None:
    """Why *name* can't be a new script's name, or None."""
    if not re.fullmatch(r"\w+", name):
        return "a name is letters, digits and _"
    if (scripts_dir / f"{name}.py").exists():
        return f"{name} already exists"
    return None


def new_script(scripts_dir: Path, name: str) -> None:
    """Write script *name* from ``NEW_SCRIPT``."""
    if error := name_error(scripts_dir, name):
        raise ValueError(error)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / f"{name}.py").write_text(NEW_SCRIPT, encoding="utf-8")


def _route_files(routes_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    paths = sorted(routes_dir.glob("*.json"))
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]


def _uses(waypoint: dict[str, Any], name: str) -> bool:
    return waypoint.get("action") == "script" and waypoint.get("label") == name


def routes_using(routes_dir: Path, name: str) -> int:
    """How many waypoints in all routes run script *name*."""
    return sum(
        _uses(wp, name) for _, raw in _route_files(routes_dir) for wp in raw.get("waypoints", [])
    )


def _rewrite_routes(routes_dir: Path, name: str, new_name: str | None) -> None:
    """Point *name*'s waypoints at *new_name*, or drop them when it is None."""
    for path, raw in _route_files(routes_dir):
        waypoints = raw.get("waypoints", [])
        if not any(_uses(wp, name) for wp in waypoints):
            continue
        if new_name is None:
            raw["waypoints"] = [wp for wp in waypoints if not _uses(wp, name)]
        else:
            for wp in waypoints:
                if _uses(wp, name):
                    wp["label"] = new_name
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def rename_script(scripts_dir: Path, routes_dir: Path, name: str, new_name: str) -> None:
    """Rename script *name* and the waypoints that run it."""
    if error := name_error(scripts_dir, new_name):
        raise ValueError(error)
    (scripts_dir / f"{name}.py").rename(scripts_dir / f"{new_name}.py")
    _rewrite_routes(routes_dir, name, new_name)


def delete_script(scripts_dir: Path, routes_dir: Path, name: str) -> None:
    """Delete script *name* and the waypoints that run it."""
    (scripts_dir / f"{name}.py").unlink()
    _rewrite_routes(routes_dir, name, None)
