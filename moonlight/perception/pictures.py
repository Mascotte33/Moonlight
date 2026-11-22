"""Reference pictures cut per OS.

Text and the client's stone texture render a little differently on Windows
and Linux: pictures cut on Windows scored 0.62..0.78 on Linux, under the
0.8 match floor. A copy cut on this OS lives next to the shared one, in a
folder named after ``sys.platform`` (``anchors/linux/stop_button.png``).
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["for_this_os"]


def for_this_os(path: Path) -> Path:
    """The copy of *path* cut on this OS (``<dir>/<sys.platform>/<name>``) when there is one."""
    own = path.parent / sys.platform / path.name
    return own if own.exists() else path
