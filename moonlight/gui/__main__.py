"""Entry point: ``python -m moonlight.gui``.

``--help`` prints usage and exits 0 without opening a window; otherwise the
dark 10-tab GUI is launched Stopped; Start (or ``--start``) runs the bot
loop thread.
"""

from __future__ import annotations

import sys

from moonlight.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
