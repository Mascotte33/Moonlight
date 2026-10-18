"""Entry point: ``python -m moonlight.gui``.

``--help`` prints usage and exits 0 without opening a window; otherwise the
dark 10-tab GUI is launched and the bot loop thread is started (unless
``--no-bot`` is given).
"""

from __future__ import annotations

import sys

from moonlight.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
