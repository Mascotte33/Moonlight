"""The bot log in the GUI, and crashes written to the log (no console window)."""

# doc: docs/prs/gui-log.md#bot-log-in-the-gui

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator

import pytest

from moonlight.gui.app import log_crashes
from moonlight.gui.state_bridge import LogFeed, LogFeedHandler


@pytest.fixture
def feed() -> Iterator[LogFeed]:
    """A LogFeed on the root logger, removed after the test."""
    lines = LogFeed()
    handler = LogFeedHandler(lines)
    logging.getLogger().addHandler(handler)
    yield lines
    logging.getLogger().removeHandler(handler)


def test_the_console_lines_go_to_the_gui_log(feed: LogFeed) -> None:
    log = logging.getLogger("moonlight.test_gui_log")
    log.setLevel(logging.DEBUG)
    log.debug("key f1")  # debug stays in the file
    log.info("state idle -> paused")
    log.warning("HP bar covered")
    try:
        1 / 0  # noqa: B018
    except ZeroDivisionError:
        log.exception("script failed")
    assert [(level, msg) for _, level, msg in feed._lines] == [
        ("info", "state idle -> paused"),
        ("warn", "HP bar covered"),
        ("error", "script failed: ZeroDivisionError: division by zero"),
    ]


# doc: docs/prs/gui-log.md#crashes-to-the-log
def test_a_crash_in_a_thread_is_logged(feed: LogFeed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.excepthook", None)
    monkeypatch.setattr("threading.excepthook", None)
    log_crashes()

    def boom() -> None:
        raise RuntimeError("boom")

    thread = threading.Thread(target=boom, name="bot")
    thread.start()
    thread.join()
    assert feed.errors() and feed.errors()[-1][2] == "crash in thread bot: RuntimeError: boom"
