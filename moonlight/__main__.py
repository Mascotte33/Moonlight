"""``python -m moonlight`` -> the GUI (:func:`moonlight.gui.app.main`).

``Moonlight.exe`` starts this through ``pythonw``, which has no console:
output and tracebacks then go to ``logs/console.log``, and a crash shows a
message box instead of closing silently.
"""

import ctypes
import sys
import traceback
from pathlib import Path

started_without_console = sys.stderr is None
if started_without_console:
    Path("logs").mkdir(exist_ok=True)
    sys.stdout = sys.stderr = open("logs/console.log", "w", encoding="utf-8", buffering=1)

try:
    from moonlight.gui.app import main

    exit_code = main()
except Exception:
    if not started_without_console:
        raise
    traceback.print_exc()
    last_line = traceback.format_exc().strip().splitlines()[-1]
    ctypes.windll.user32.MessageBoxW(
        None, f"{last_line}\n\nFull traceback: logs\\console.log", "Moonlight crashed", 0x10
    )
    exit_code = 1
raise SystemExit(exit_code)
