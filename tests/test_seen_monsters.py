"""SeenMonsters: the seen-monsters file and the background name guess.

# doc: docs/prs/targeting.md#seen-monsters
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np
import pytest

from moonlight.targeting.seen_monsters import SeenMonsters

CROP = np.full((8, 40, 3), 255, dtype=np.uint8)


def finish_guessing(store: SeenMonsters) -> None:
    """Wait (at most 1 s) until the guess queue is empty."""
    waiter = threading.Thread(target=store._crops.join, daemon=True)
    waiter.start()
    waiter.join(1)
    assert not waiter.is_alive()


def make_store(path: Path) -> tuple[SeenMonsters, list[int]]:
    store = SeenMonsters(path)
    calls: list[int] = []

    def stub(crop: np.ndarray) -> str:
        calls.append(1)
        return "cyclops"

    store._guess = stub  # type: ignore[method-assign, assignment]
    return store, calls


def test_new_hash_is_empty_then_guessed_once(tmp_path: Path) -> None:
    store, calls = make_store(tmp_path / "p.seen_monsters.json")
    assert store.name_for("aa", CROP, CROP) == ""
    finish_guessing(store)
    assert store.name_for("aa", CROP, CROP) == "cyclops"
    finish_guessing(store)
    assert len(calls) == 1


def test_no_new_hash_is_learned_while_another_window_may_cover_the_client(
    tmp_path: Path,
) -> None:
    store, calls = make_store(tmp_path / "p.seen_monsters.json")
    store.rename("known", "Rat")
    store.learns_new_names = False
    assert store.name_for("title bar", CROP, CROP) == ""
    assert store.name_for("known", CROP, CROP) == "Rat"
    assert [m.name_hash for m in store.entries()] == ["known"]
    assert not store.image_path("title bar").exists()
    assert calls == []


def test_rename_wins_and_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / "p.seen_monsters.json"
    store, _ = make_store(path)
    store.name_for("aa", CROP, CROP)
    finish_guessing(store)
    store.rename("aa", "Dragon")
    assert store.name_for("aa", CROP, CROP) == "Dragon"
    reloaded = SeenMonsters(path)
    assert [(m.name_hash, m.guessed_name, m.name) for m in reloaded.entries()] == [
        ("aa", "cyclops", "Dragon")
    ]
    assert reloaded.name_for("aa", CROP, CROP) == "Dragon"


def test_broken_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "p.seen_monsters.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        SeenMonsters(path)
    path.write_text('{"aa": {"name": "x"}}', encoding="utf-8")
    with pytest.raises(ValueError):
        SeenMonsters(path)


def test_failing_guess_logs_once_and_keeps_names_working(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = SeenMonsters(tmp_path / "p.seen_monsters.json")

    def no_tesseract(crop: np.ndarray) -> str:
        raise RuntimeError("tesseract is not installed")

    store._guess = no_tesseract  # type: ignore[method-assign, assignment]
    with caplog.at_level(logging.WARNING, logger="moonlight.targeting.seen_monsters"):
        assert store.name_for("aa", CROP, CROP) == ""
        finish_guessing(store)
        assert store.name_for("bb", CROP, CROP) == ""
        store.rename("bb", "Rat")
    assert store.name_for("bb", CROP, CROP) == "Rat"
    assert len(caplog.records) == 1


def test_each_hash_saves_its_slot_picture_once(tmp_path: Path) -> None:
    store, _calls = make_store(tmp_path / "p.seen_monsters.json")
    store.name_for("aa", CROP, CROP)
    picture = store.image_path("aa")
    assert picture == tmp_path / "p.seen_monsters" / "aa.png"
    written_at = picture.stat().st_mtime_ns
    store.name_for("aa", CROP, np.zeros_like(CROP))
    assert picture.stat().st_mtime_ns == written_at
