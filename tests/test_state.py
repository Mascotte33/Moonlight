"""Unit tests for moonlight.core.state (WorldState and supporting types).

Platform-independent: pure data structures plus threading behaviour, so
the suite runs anywhere.
"""

from __future__ import annotations

import threading
import time
from dataclasses import FrozenInstanceError

import pytest

from moonlight.core.state import Capacity, Monster, Position, WorldState


class TestMonster:
    def test_fields(self) -> None:
        m = Monster(name="Wolf", hp_percent=42.5, is_player=True)
        assert m.name == "Wolf"
        assert m.hp_percent == 42.5
        assert m.is_player is True

    def test_defaults(self) -> None:
        m = Monster(name="Wolf")
        assert m.hp_percent == 100.0
        assert m.is_player is False
        assert m.name_hash == ""
        assert m.battle_list_row == 0

    def test_frozen(self) -> None:
        m = Monster(name="Wolf")
        with pytest.raises(FrozenInstanceError):
            m.name = "Bear"  # type: ignore[misc]


class TestPosition:
    def test_fields(self) -> None:
        p = Position(1032, 1011, 10)
        assert (p.x, p.y, p.z) == (1032, 1011, 10)

    def test_default_z(self) -> None:
        assert Position(1, 2).z == 0


class TestCapacity:
    def test_percent_mid(self) -> None:
        assert Capacity(weight=50, max_weight=100).percent == 50.0

    def test_percent_unknown_cap(self) -> None:
        assert Capacity().percent == 0.0

    def test_percent_clamped_high(self) -> None:
        assert Capacity(weight=150, max_weight=100).percent == 100.0

    def test_percent_clamped_low(self) -> None:
        assert Capacity(weight=-5, max_weight=100).percent == 0.0


class TestWorldStateDefault:
    def test_default_values(self) -> None:
        ws = WorldState.default()
        assert isinstance(ws, WorldState)
        assert ws.client_focused is False
        assert ws.screen_ok is False
        assert ws.hp_percent == 0.0
        assert ws.mp_percent == 0.0
        assert ws.monsters == []
        assert ws.selected_target is None
        assert ws.players_on_screen == 0
        assert ws.position == Position(0, 0, 0)
        assert ws.capacity == Capacity(0, 0)
        assert ws.last_update == 0.0

    def test_instances_are_independent(self) -> None:
        a, b = WorldState.default(), WorldState.default()
        a.monsters.append(Monster("Wolf"))
        assert b.monsters == []


class TestSnapshot:
    def test_reflects_updates(self) -> None:
        ws = WorldState.default()
        with ws.lock():
            ws.client_focused = True
            ws.hp_percent = 73.0
            ws.monsters = [Monster("Wolf", hp_percent=80.0)]
            ws.last_update = time.monotonic()
        snap = ws.snapshot()
        assert snap.client_focused is True
        assert snap.hp_percent == 73.0
        assert snap.monsters == [Monster("Wolf", hp_percent=80.0)]
        assert snap.last_update > 0.0

    def test_isolated_from_live_state(self) -> None:
        ws = WorldState.default()
        with ws.lock():
            ws.monsters = [Monster("Wolf")]
        snap = ws.snapshot()
        snap.monsters.append(Monster("Bear"))
        assert len(ws.monsters) == 1

    def test_snapshot_has_own_lock(self) -> None:
        ws = WorldState.default()
        assert ws.snapshot()._lock is not ws._lock

    def test_concurrent_writer_and_reader(self) -> None:
        """Snapshot never mixes fields from two different writer frames."""
        ws = WorldState.default()
        stop = threading.Event()
        errors: list[str] = []

        def writer() -> None:
            i = 0
            while not stop.is_set():
                with ws.lock():
                    # Both fields set atomically under the lock; a consistent
                    # snapshot must always see the matching pair.
                    ws.hp_percent = float(i % 101)
                    ws.monsters = [Monster(f"m{i}")]
                    ws.last_update = time.monotonic()
                i += 1

        def reader() -> None:
            while not stop.is_set():
                snap = ws.snapshot()
                if snap.monsters:
                    frame = int(snap.monsters[0].name[1:])
                    if snap.hp_percent != float(frame % 101):
                        errors.append(f"torn read: hp={snap.hp_percent} frame={frame}")

        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader)
        wt.start()
        rt.start()
        time.sleep(0.3)
        stop.set()
        wt.join()
        rt.join()
        assert not errors


class TestLocking:
    def test_lock_is_reentrant(self) -> None:
        ws = WorldState.default()
        with ws.lock():
            with ws.lock():
                ws.hp_percent = 1.0

    def test_snapshot_never_blocks_on_writer(self) -> None:
        ws = WorldState.default()
        with ws.lock():
            snap = ws.snapshot()  # must not deadlock (RLock)
        assert snap.hp_percent == 0.0
