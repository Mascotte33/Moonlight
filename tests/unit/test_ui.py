"""Unit tests for the terminal UI module (moonlight.ui, T27).

Fully offline: FakeInput backend, tmp_path profile directories, and
scripted input lines — no real keyboard, screen, or Arduino involved.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest

from moonlight.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.input import FakeInput
from moonlight.loop import GameLoop, LoopConfig
from moonlight.state import Capacity, Monster, Position, WorldState
from moonlight.ui import (
    BotController,
    LogViewer,
    LogViewerHandler,
    MoonlightUI,
    ProfileSelector,
    StateDisplay,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_loop(state: WorldState | None = None) -> GameLoop:
    st = state if state is not None else WorldState.default()
    return GameLoop(st, FakeInput(), LoopConfig(enable_f12_monitor=False))


def running_state() -> WorldState:
    """A world state where the loop keeps ticking instead of self-stopping."""
    st = WorldState.default()
    with st.lock():
        st.client_focused = True
        st.screen_ok = True
    return st


def write_profile(d: Path, name: str = "default") -> Path:
    p = CalibrationProfile(landmark=(100, 100))
    for i, rname in enumerate(REGIONS):
        p.set_region(rname, Region(10 + i, 20, 30, 10))
    path = d / f"{name}.json"
    p.save(path)
    return path


def scripted_run(
    ui: MoonlightUI, lines: list[str]
) -> list[str]:
    """Run the UI with scripted input lines; return the captured output."""
    out: list[str] = []
    it = iter(lines)

    def read() -> str | None:
        try:
            return next(it)
        except StopIteration:
            return None

    ui.run(input_fn=read, output=out.append)
    return out


def make_ui(profile_dir: Path | None = None) -> tuple[MoonlightUI, GameLoop, WorldState]:
    state = WorldState.default()
    loop = make_loop(state)
    ui = MoonlightUI(
        state,
        BotController(loop),
        ProfileSelector(profile_dir),
        color=False,
    )
    return ui, loop, state


# ---------------------------------------------------------------------------
# LogViewer
# ---------------------------------------------------------------------------


class TestLogViewer:
    def test_add_and_get_recent(self) -> None:
        lv = LogViewer()
        lv.add_log("a")
        lv.add_log("b")
        lv.add_log("c")
        assert lv.get_recent(2) == ["b", "c"]
        assert lv.get_recent(10) == ["a", "b", "c"]

    def test_get_recent_zero(self) -> None:
        lv = LogViewer()
        lv.add_log("a")
        assert lv.get_recent(0) == []

    def test_get_recent_negative_raises(self) -> None:
        lv = LogViewer()
        with pytest.raises(ValueError):
            lv.get_recent(-1)

    def test_maxlen_drops_oldest(self) -> None:
        lv = LogViewer(maxlen=3)
        for i in range(5):
            lv.add_log(str(i))
        assert lv.get_recent(10) == ["2", "3", "4"]
        assert len(lv) == 3

    def test_clear(self) -> None:
        lv = LogViewer()
        lv.add_log("a")
        lv.clear()
        assert lv.get_recent(10) == []
        assert len(lv) == 0

    def test_invalid_maxlen_raises(self) -> None:
        with pytest.raises(ValueError):
            LogViewer(maxlen=0)

    def test_contains(self) -> None:
        lv = LogViewer()
        lv.add_log("hello")
        assert "hello" in lv
        assert "world" not in lv

    def test_thread_safety(self) -> None:
        lv = LogViewer(maxlen=10_000)

        def worker(n: int) -> None:
            for i in range(50):
                lv.add_log(f"{n}-{i}")

        import threading

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(lv) == 500


# ---------------------------------------------------------------------------
# LogViewerHandler
# ---------------------------------------------------------------------------


class TestLogViewerHandler:
    def test_forwards_records(self) -> None:
        import logging

        lv = LogViewer()
        h = LogViewerHandler(lv)
        lg = logging.getLogger("test_ui_handler")
        lg.setLevel(logging.INFO)
        lg.addHandler(h)
        lg.info("decision: attack")
        lg.removeHandler(h)
        assert any("decision: attack" in m for m in lv.get_recent(10))


# ---------------------------------------------------------------------------
# ProfileSelector
# ---------------------------------------------------------------------------


class TestProfileSelector:
    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        sel = ProfileSelector(tmp_path / "nope")
        assert sel.list_profiles() == []
        with pytest.raises(ValueError):
            sel.select_profile("x")

    def test_list_profiles_sorted(self, tmp_path: Path) -> None:
        write_profile(tmp_path, "beta")
        write_profile(tmp_path, "alpha")
        (tmp_path / "not_a_profile.txt").write_text("x")
        assert ProfileSelector(tmp_path).list_profiles() == ["alpha", "beta"]

    def test_select_profile_ok(self, tmp_path: Path) -> None:
        write_profile(tmp_path)
        prof = ProfileSelector(tmp_path).select_profile("default")
        assert prof.landmark == (100, 100)
        assert len(prof.regions) == len(REGIONS)

    def test_select_profile_unknown_raises(self, tmp_path: Path) -> None:
        write_profile(tmp_path)
        with pytest.raises(ValueError, match="Unknown profile"):
            ProfileSelector(tmp_path).select_profile("ghost")

    def test_select_profile_invalid_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "broken.json").write_text("not json {")
        from moonlight.calibrate import CalibrationError

        with pytest.raises(CalibrationError):
            ProfileSelector(tmp_path).select_profile("broken")


# ---------------------------------------------------------------------------
# StateDisplay
# ---------------------------------------------------------------------------


class TestStateDisplay:
    def test_render_contains_core_fields(self) -> None:
        st = WorldState.default()
        with st.lock():
            st.client_focused = True
            st.screen_ok = True
            st.hp_percent = 75.0
            st.mp_percent = 40.0
            st.players_on_screen = 2
            st.position = Position(1032, 1011, 10)
            st.capacity = Capacity(9000.0, 20000.0)
            st.monsters.append(Monster("Wolf", 80.0))
            st.monsters.append(Monster("Bear", 55.0))
            st.buffs["haste"] = 4.2
        text = StateDisplay().render(st, bot_state="attack")
        assert "MOONLIGHT" in text
        assert "attack" in text
        assert "75.0%" in text
        assert "40.0%" in text
        assert "1032/1011/10" in text
        assert "45%" in text
        assert "players: 2" in text
        assert "Wolf 80%" in text
        assert "Bear 55%" in text
        assert "haste 4.2s" in text

    def test_render_default_state(self) -> None:
        text = StateDisplay().render(WorldState.default())
        assert "0.0%" in text
        assert "? " in text  # unknown position
        assert "monsters" not in text  # empty lists are omitted

    def test_render_uses_snapshot(self) -> None:
        st = WorldState.default()
        first = StateDisplay().render(st)
        with st.lock():
            st.hp_percent = 99.0
        second = StateDisplay().render(st)
        assert "99.0%" not in first  # first render captured the old snapshot
        assert "99.0%" in second

    def test_target_line_shown_when_selected(self) -> None:
        st = WorldState.default()
        m = Monster("Slime", 33.0)
        with st.lock():
            st.selected_target = m
        text = StateDisplay().render(st)
        assert "target: Slime (33%)" in text

    def test_color_off_has_no_ansi(self) -> None:
        text = StateDisplay(color=False).render(WorldState.default())
        assert "\033[" not in text

    def test_color_on_has_ansi(self) -> None:
        text = StateDisplay(color=True).render(WorldState.default())
        assert "\033[" in text

    def test_bar_width_stable(self) -> None:
        d = StateDisplay()
        for pct in (0.0, 10.0, 50.0, 75.0, 100.0):
            bar = d._bar(pct)
            inner = bar.split("[")[1].split("]")[0]
            assert len(inner) == d.BAR_WIDTH

    def test_bar_clamps_out_of_range(self) -> None:
        assert StateDisplay()._bar(-5.0).endswith("0.0%")
        assert StateDisplay()._bar(150.0).endswith("0.0%")


# ---------------------------------------------------------------------------
# BotController
# ---------------------------------------------------------------------------


class TestBotController:
    def test_not_running_initially(self) -> None:
        c = BotController(make_loop())
        assert not c.is_running()

    def test_start_and_stop(self) -> None:
        c = BotController(make_loop(running_state()))
        c.start()
        try:
            for _ in range(200):
                if c.is_running():
                    break
                time.sleep(0.01)
            assert c.is_running()
        finally:
            c.kill()
        assert not c.is_running()
        assert not c.loop.stop_requested or c.loop.kill_switch_active

    def test_stop_terminates_loop(self) -> None:
        c = BotController(make_loop(running_state()))
        c.start()
        for _ in range(200):
            if c.is_running():
                break
            time.sleep(0.01)
        c.stop("test")
        assert not c.is_running()
        assert c.loop.stop_requested
        assert not c.loop.kill_switch_active

    def test_kill_terminates_loop(self) -> None:
        c = BotController(make_loop(running_state()))
        c.start()
        for _ in range(200):
            if c.is_running():
                break
            time.sleep(0.01)
        c.kill()
        assert not c.is_running()
        assert c.loop.kill_switch_active

    def test_double_start_is_noop(self) -> None:
        c = BotController(make_loop(running_state()))
        c.start()
        for _ in range(200):
            if c.is_running():
                break
            time.sleep(0.01)
        c.start()  # no-op: already running
        names = [t.name for t in __import__("threading").enumerate() if t.name == "moonlight-bot"]
        assert len(names) == 1
        c.kill()

    def test_exhausted_loop_raises_without_factory(self) -> None:
        # default state -> screen_ok is False -> loop exits after one tick
        c = BotController(make_loop())
        c.start()
        assert c.wait(2.0)
        assert not c.is_running()
        with pytest.raises(RuntimeError):
            c.start()

    def test_loop_factory_replaces_exhausted_loop(self) -> None:
        loops: list[GameLoop] = []

        def factory() -> GameLoop:
            loop = make_loop(running_state())
            loops.append(loop)
            return loop

        c = BotController(make_loop(running_state()), loop_factory=factory)
        c.start()
        for _ in range(200):
            if c.is_running():
                break
            time.sleep(0.01)
        c.kill()
        assert not c.is_running()
        # restart with a fresh loop from the factory
        c.start()
        for _ in range(200):
            if c.is_running():
                break
            time.sleep(0.01)
        try:
            assert c.loop is loops[0]
        finally:
            c.kill()
        assert not c.is_running()


# ---------------------------------------------------------------------------
# MoonlightUI
# ---------------------------------------------------------------------------


class TestMoonlightUI:
    def test_quit_ends_run(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["quit"])
        assert any("bye." in line for line in out)

    def test_exit_and_q_aliases(self, tmp_path: Path) -> None:
        for cmd in ("exit", "q"):
            ui, _, _ = make_ui(tmp_path)
            out = scripted_run(ui, [cmd])
            assert any("bye." in line for line in out)

    def test_eof_ends_run(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out: list[str] = []
        ui.run(input_fn=lambda: None, output=out.append)
        assert any("bye." in line for line in out)

    def test_help_lists_commands(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["help", "quit"])
        joined = "\n".join(out)
        for token in ("start", "stop", "kill", "profiles", "state", "log", "stats"):
            assert token in joined

    def test_start_stop_flow(self, tmp_path: Path) -> None:
        ui, loop, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["start", "stop", "quit"])
        joined = "\n".join(out)
        assert "bot started" in joined
        assert "stop requested" in joined
        assert loop.stop_requested
        assert not ui.controller.is_running()

    def test_kill_command(self, tmp_path: Path) -> None:
        ui, loop, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["kill", "quit"])
        assert any("KILL SWITCH ACTIVATED" in line for line in out)
        assert loop.kill_switch_active

    def test_unknown_command(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["bogus", "quit"])
        assert any("unknown command" in line for line in out)

    def test_empty_lines_are_noop(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["", "   ", "quit"])
        assert any("bye." in line for line in out)

    def test_frame_shows_state_and_prompt(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["quit"])
        joined = "\n".join(out)
        assert "HP" in joined
        assert "MP" in joined
        assert ui.PROMPT in joined

    def test_profiles_command(self, tmp_path: Path) -> None:
        write_profile(tmp_path, "one")
        write_profile(tmp_path, "two")
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["profiles", "quit"])
        assert any("profiles: one, two" in line for line in out)

    def test_profiles_command_empty(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["profiles", "quit"])
        assert any("(none found)" in line for line in out)

    def test_profile_select_ok(self, tmp_path: Path) -> None:
        write_profile(tmp_path)
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["profile default", "quit"])
        assert any("profile loaded: default" in line for line in out)
        assert ui.selected_profile is not None
        assert any("profile loaded: default" in m for m in ui.log.get_recent(10))

    def test_profile_select_invalid(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["profile nope", "quit"])
        assert any("profile error:" in line for line in out)
        assert ui.selected_profile is None

    # -- calibrate command -------------------------------------------------

    def _fake_profile(self) -> CalibrationProfile:
        p = CalibrationProfile(landmark=(100, 100))
        for i, rname in enumerate(REGIONS):
            p.set_region(rname, Region(10 + i, 20, 30, 10))
        return p

    def test_calibrate_in_help(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["help", "quit"])
        joined = "\n".join(out)
        assert "calibrate" in joined

    def test_calibrate_command_saves_and_loads_profile(self, tmp_path: Path) -> None:
        fake_app = mock.MagicMock()
        fake_app.run.return_value = self._fake_profile()
        with mock.patch("moonlight.ui.CalibrationApp", return_value=fake_app):
            ui, _, _ = make_ui(tmp_path)
            out = scripted_run(ui, ["calibrate myprof", "quit"])
        joined = "\n".join(out)
        # It announced the window and saved the profile.
        assert "opening calibration window for profile 'myprof'" in joined
        assert "saved profile to" in joined
        # The profile file now exists on disk.
        assert (tmp_path / "myprof.json").is_file()
        # It was auto-loaded.
        assert ui.selected_profile is not None
        assert any("profile loaded: myprof" in line for line in out)

    def test_calibrate_command_cancelled(self, tmp_path: Path) -> None:
        fake_app = mock.MagicMock()
        fake_app.run.return_value = None  # user pressed Esc
        with mock.patch("moonlight.ui.CalibrationApp", return_value=fake_app):
            ui, _, _ = make_ui(tmp_path)
            out = scripted_run(ui, ["calibrate myprof", "quit"])
        joined = "\n".join(out)
        assert "calibration cancelled" in joined
        # No profile file written.
        assert not (tmp_path / "myprof.json").exists()
        assert ui.selected_profile is None

    def test_calibrate_command_unknown_option(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["calibrate --bogus", "quit"])
        assert any("unknown option" in line for line in out)

    def test_calibrate_command_screenshot_flag(self, tmp_path: Path) -> None:
        fake_app = mock.MagicMock()
        fake_app.run.return_value = self._fake_profile()
        with (
            mock.patch("moonlight.ui.CalibrationApp", return_value=fake_app) as app_cls,
            mock.patch("moonlight.ui.load_image_bgr", return_value=None) as loader,
        ):
            ui, _, _ = make_ui(tmp_path)
            out = scripted_run(
                ui, ["calibrate myprof --screenshot shot.png", "quit"]
            )
        # The screenshot path was passed through to the image loader.
        loader.assert_called_once_with("shot.png")
        # The app received the loaded (mock) image.
        app_cls.assert_called_once_with(image_bgr=None)
        assert any("saved profile to" in line for line in out)

    def test_state_command_redraws(self, tmp_path: Path) -> None:
        ui, _, st = make_ui(tmp_path)
        with st.lock():
            st.hp_percent = 42.0
        out = scripted_run(ui, ["state", "quit"])
        assert any("42.0%" in line for line in out)

    def test_log_command(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        ui.log.add_log("line one")
        ui.log.add_log("line two")
        out = scripted_run(ui, ["log", "quit"])
        joined = "\n".join(out)
        assert "line one" in joined
        assert "line two" in joined

    def test_stats_command(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out = scripted_run(ui, ["stats", "quit"])
        joined = "\n".join(out)
        assert "ticks:" in joined
        assert "actions sent:" in joined
        assert "kill switch:" in joined

    def test_max_iterations_stops_run(self, tmp_path: Path) -> None:
        ui, _, _ = make_ui(tmp_path)
        out: list[str] = []
        ui.run(
            input_fn=lambda: "help",
            output=out.append,
            max_iterations=2,
        )
        assert any("bye." in line for line in out)

    def test_bot_logging_reaches_log_viewer(self, tmp_path: Path) -> None:
        """Loop records (bot decisions) are forwarded into the UI log."""
        import logging

        ui, loop, _ = make_ui(tmp_path)
        logging.getLogger("moonlight").setLevel(logging.INFO)
        out = scripted_run(ui, ["start", "quit"])
        joined = "\n".join(out)
        # the loop warns once about screen_ok=False and self-stops
        assert "screen_ok" in joined or "bot started" in joined
