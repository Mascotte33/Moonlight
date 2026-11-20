"""tools/stuck_spots.py: stuck lines count against the waypoint being walked to."""

# doc: docs/prs/stuck-spots.md#stuck-spots

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "stuck_spots", Path(__file__).parent.parent / "tools" / "stuck_spots.py"
)
assert _spec is not None and _spec.loader is not None
stuck_spots = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stuck_spots)

LOG = """\
2026-11-07 09:59:59,000 x cavebot: route 'Camp', 40 waypoints
2026-11-07 10:00:00,000 x cavebot: reached waypoint 4
2026-11-07 10:00:01,000 x cavebot: [stuck: blocked-tile] tile 32526,31933,7 blocked, walking around
2026-11-07 10:00:02,000 x cavebot: [stuck: blocked-tile] tile 32526,31933,7 blocked, walking around
2026-11-07 10:00:03,000 x cavebot: [stuck: no-move] route 'Camp': position 32526,31932,7 unchanged
2026-11-07 10:00:04,000 x cavebot: creatures moved, walking another way
2026-11-08 10:00:05,000 x cavebot: reached waypoint 9
2026-11-08 10:00:06,000 x cavebot: [stuck: creatures] creatures block the way, targeting takes over
"""


def test_counts_per_route_and_waypoint_walked_to() -> None:
    spots = stuck_spots.count_stuck_spots(LOG.splitlines())
    first = spots[("Camp", 5)]
    assert first.kinds == {"blocked-tile": 2, "no-move": 1}
    assert first.tiles == {"32526,31933,7": 2, "32526,31932,7": 1}
    assert spots[("Camp", 10)].kinds == {"creatures": 1}
    assert spots[("Camp", 10)].tiles == {}


def test_since_skips_older_days() -> None:
    spots = stuck_spots.count_stuck_spots(LOG.splitlines(), since="2026-11-08")
    assert list(spots) == [("?", 10)]  # the route line was on an older day


def test_reads_rolled_logs_oldest_first(tmp_path: Path) -> None:
    (tmp_path / "bot.log.1").write_text("old\n")
    (tmp_path / "bot.log").write_text("new\n")
    (tmp_path / "bot.log.2").write_text("oldest\n")
    assert list(stuck_spots.log_lines(tmp_path)) == ["oldest", "old", "new"]
