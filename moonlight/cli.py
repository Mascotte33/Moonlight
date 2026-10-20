"""MOONLIGHT command line (T40): ``python -m moonlight`` / ``moonlight``.

Subcommands::

    moonlight run [profile]   build the bot from the config and run the loop
    moonlight profiles        list calibration profiles
    moonlight calibrate ...   the calibration tool (moonlight.calibrate)
    moonlight check           serial port? profile? perception imports?

:func:`build_loop` is the integration seam: it assembles perception ->
WorldState -> decision modules -> :class:`GameLoop` from a
:class:`BotConfig`, a :class:`CalibrationProfile`, any
:class:`InputBackend` and any frame provider -- so a headless test can
drive the real wiring with ``FakeInput`` and synthetic frames.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np

from moonlight.calibrate import CalibrationProfile
from moonlight.config import DEFAULT_CONFIG_FILE, BotConfig, ConfigError
from moonlight.input import InputBackend
from moonlight.loop import DecisionModules, GameLoop, beep_alert
from moonlight.profile import ProfileManager
from moonlight.state import WorldState

__all__ = ["build_loop", "main", "make_perceive"]

FrameProvider = Callable[[], np.ndarray]

logger = logging.getLogger(__name__)

#: Modules ``check`` imports to prove the perception stack loads.
_PERCEPTION_MODULES = (
    "cv2",
    "moonlight.capture",
    "moonlight.perception.vitals",
    "moonlight.perception.battle",
    "moonlight.perception.floor",
    "moonlight.perception.minimap",
)


def _version() -> str:
    try:
        return version("moonlight")
    except PackageNotFoundError:
        return "0.1.0"


# -- integration seam ---------------------------------------------------------


def make_perceive(
    state: WorldState,
    profile: CalibrationProfile,
    frame_provider: FrameProvider,
    focus_check: Callable[[], bool] | None = None,
    minimap_start: tuple[int, int, int] | None = None,
) -> Callable[[], None]:
    """Return a callback doing one capture -> perception -> WorldState pass.

    Fail closed: an unfocused client or any perception error sets
    ``screen_ok = False``, which gates every input in the loop.

    *minimap_start* (F-03) optionally seeds the minimap tracker with an
    absolute ``(x, y, z)`` start so reported positions are absolute world
    coordinates (seed + accumulated relative offset) instead of relative to
    wherever the bot started.
    """
    from moonlight.perception.battle import BattleReader, entries_to_state
    from moonlight.perception.floor import FloorReader
    from moonlight.perception.minimap import MinimapReader
    from moonlight.perception.vitals import VitalsReader
    from moonlight.state import Position

    vitals = VitalsReader(profile)
    battle = BattleReader(profile)
    floor = FloorReader(profile)
    seed = (
        Position(minimap_start[0], minimap_start[1], minimap_start[2])
        if minimap_start is not None
        else None
    )
    minimap = MinimapReader(profile, floor_reader=floor, initial_position=seed)

    def perceive() -> None:
        focused = focus_check() if focus_check is not None else True
        try:
            if not focused:
                raise RuntimeError("client not focused")
            frame = frame_provider()
            hp, mp = vitals.read_vitals(frame)
            monsters, selected, players = entries_to_state(battle.read_battle_list(frame))
            position = minimap.read_position(frame)
            z = floor.read_floor(frame)
        except Exception:  # noqa: BLE001 - any perception failure = screen not ok
            with state.lock():
                state.client_focused = focused
                state.screen_ok = False
            return
        with state.lock():
            state.client_focused = True
            state.screen_ok = True
            state.hp_percent = float(hp) if hp is not None else 0.0
            state.mp_percent = float(mp) if mp is not None else 0.0
            state.monsters = monsters
            state.selected_target = selected
            state.players_on_screen = players
            if position is not None:
                state.position = position
            if z is not None:
                state.position = replace(state.position, z=int(z))
            state.last_update = time.monotonic()

    return perceive


def _templates(directory: Path) -> dict[str, np.ndarray]:
    """Grayscale ``*.png`` templates in *directory* keyed by stem ({} if none)."""
    import cv2

    out: dict[str, np.ndarray] = {}
    for path in sorted(directory.glob("*.png")) if directory.is_dir() else []:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            out[path.stem] = image
    return out


def build_loop(
    config: BotConfig,
    profile: CalibrationProfile,
    backend: InputBackend,
    *,
    frame_provider: FrameProvider,
    focus_check: Callable[[], bool] | None = None,
    state: WorldState | None = None,
    clock: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    alert: Callable[[str], None] | None = beep_alert,
) -> GameLoop:
    """Assemble the bot: perception + the enabled decision modules + loop.

    Raises:
        ValueError: If an enabled module cannot be built from *profile*
            (e.g. cavebot without a minimap/game_grid region).
    """
    from moonlight.decision.alarms import AlarmDecision
    from moonlight.decision.buffs import BuffManager
    from moonlight.decision.healing import HealingDecision
    from moonlight.decision.targeting import TargetingDecision

    state = state if state is not None else WorldState()
    combos = config.spell_combos
    m = DecisionModules()
    if config.healing is not None:
        m.healing = HealingDecision(config.healing, spell_combos=combos, clock=clock)
    if config.targeting is not None:
        m.targeting = TargetingDecision(config.targeting)
        # F-01 (interim): no name resolver is wired yet (T34), so every
        # battle entry is a nameless/UNKNOWN creature. Targeting treats
        # unknown entries as UNKNOWN and never auto-attacks them; say so
        # explicitly at start rather than failing silently.
        logger.warning(
            "targeting is enabled but no name resolver is wired (T34); "
            "unknown-named battle entries are treated as UNKNOWN and will "
            "NOT be auto-attacked"
        )
    if config.buffs is not None:
        m.buffs = BuffManager(config.buffs, spell_combos=combos, clock=clock)
    if config.alarms is not None:
        m.alarms = AlarmDecision(config.alarms, clock=clock)

    if config.cavebot is not None or config.looting is not None:
        from moonlight.clicker import ClickController
        from moonlight.perception.cursor import CursorReader

        cursor = list(_templates(config.templates_dir / "cursor").values())
        clicker = ClickController(
            backend, CursorReader(profile, cursor_templates=cursor or None), frame_provider
        )
        if config.cavebot is not None:
            from moonlight.decision.cavebot import Cavebot

            cb = config.cavebot
            m.cavebot = Cavebot(
                cb.route,
                profile.get_region("minimap").to_tuple,
                clicker,
                state,
                game_region=profile.get_region("game_grid").to_tuple,
                input_backend=backend,
                rope_hotkey=cb.rope_hotkey,
                shovel_hotkey=cb.shovel_hotkey,
                clock=clock,
            )
            m.cavebot.start()
        if config.looting is not None:
            from moonlight.decision.looting import Looting
            from moonlight.perception.loot import LootReader

            reader = LootReader(_templates(config.templates_dir / "loot"), profile)
            m.looting = Looting(
                reader, clicker, backend, state, config.looting, frame_provider=frame_provider
            )
    if config.refill is not None:
        from moonlight.decision.cavebot import RouteFileLoader
        from moonlight.decision.refill import Refill

        assert m.cavebot is not None and m.looting is not None  # BotConfig.validate
        m.refill = Refill(config.refill, m.cavebot, RouteFileLoader(), m.looting, state, backend)

    return GameLoop(
        state,
        backend,
        config.loop,
        modules=m,
        perceive=make_perceive(
            state,
            profile,
            frame_provider,
            focus_check,
            minimap_start=config.minimap_start,
        ),
        clock=clock,
        sleep_fn=sleep_fn,
        alert=alert,
    )


# -- subcommands ----------------------------------------------------------------


def _load_config(args: argparse.Namespace) -> BotConfig:
    path = Path(args.config)
    if path.exists():
        return BotConfig.load(path)
    if args.config != DEFAULT_CONFIG_FILE:
        raise ConfigError(f"config file not found: {path}")
    return BotConfig.default()


def _profiles(args: argparse.Namespace, config: BotConfig | None = None) -> ProfileManager:
    directory = args.profiles_dir or (config.profiles_dir if config is not None else None)
    return ProfileManager(directory)


def _cmd_run(args: argparse.Namespace) -> int:
    from moonlight.capture import ScreenCapture
    from moonlight.input import ArduinoInput

    config = _load_config(args)
    if getattr(args, "minimap_start", None) is not None:
        config = replace(config, minimap_start=tuple(args.minimap_start))
    name = args.profile or config.profile
    profile = _profiles(args, config).load(name)
    capture = ScreenCapture()
    backend = ArduinoInput(config.serial_port)
    try:
        loop = build_loop(
            config, profile, backend, frame_provider=capture.get_frame,
            focus_check=capture.is_focused,
        )
        print(f"moonlight {_version()}: running profile {name!r}; F12 = kill switch")
        loop.run()
    finally:
        backend.close()  # releases all inputs and stops the heartbeat
    return 0


def _cmd_profiles(args: argparse.Namespace) -> int:
    manager = _profiles(args)
    names = manager.list_profiles()
    if not names:
        print(f"No profiles in {manager.directory}")
    for name in names:
        print(name)
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from moonlight import calibrate

    return calibrate.main(list(args.calibrate_args))


def _cmd_check(args: argparse.Namespace) -> int:
    ok = True

    def report(passed: bool, what: str) -> None:
        nonlocal ok
        ok = ok and passed
        print(f"[{'OK' if passed else 'FAIL'}] {what}")

    try:
        config = _load_config(args)
        report(True, f"config ({args.config})")
    except ConfigError as exc:
        report(False, f"config: {exc}")
        config = None
    try:
        from moonlight.input import find_arduino_port

        port = config.serial_port if config and config.serial_port else find_arduino_port()
        report(port is not None, f"serial port: {port or 'no Arduino found'}")
    except Exception as exc:  # noqa: BLE001 - pyserial missing etc.
        report(False, f"serial port: {exc}")
    name = args.profile or (config.profile if config else None)
    try:
        if name is None:
            raise ValueError("no profile name")
        _profiles(args, config).load(name)
        report(True, f"profile {name!r}")
    except Exception as exc:  # noqa: BLE001 - ValueError / CalibrationError
        report(False, f"profile {name!r}: {exc}")
    for mod in _PERCEPTION_MODULES:
        try:
            importlib.import_module(mod)
            report(True, f"import {mod}")
        except Exception as exc:  # noqa: BLE001
            report(False, f"import {mod}: {exc}")
    return 0 if ok else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moonlight", description="MOONLIGHT Tibia bot.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_FILE,
        help=f"bot config JSON (default: ./{DEFAULT_CONFIG_FILE}; built-in defaults if absent)",
    )
    parser.add_argument("--profiles-dir", type=Path, default=None, help="profiles directory")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the bot")
    run.add_argument("profile", nargs="?", default=None, help="profile (default: config)")
    run.add_argument(
        "--minimap-start",
        nargs=3,
        type=int,
        default=None,
        metavar=("X", "Y", "Z"),
        help="absolute (x, y, z) seed for minimap position tracking (F-03); "
        "overrides the config's minimap_start",
    )
    run.set_defaults(func=_cmd_run)
    sub.add_parser("profiles", help="list calibration profiles").set_defaults(
        func=_cmd_profiles
    )
    # Arguments after "calibrate" are passed through (see main()).
    sub.add_parser("calibrate", help="calibration tool (args passed through)").set_defaults(
        func=_cmd_calibrate
    )
    check = sub.add_parser("check", help="check serial port, profile, perception")
    check.add_argument("profile", nargs="?", default=None)
    check.set_defaults(func=_cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = _parser()
    args, extra = parser.parse_known_args(argv)
    if args.command == "calibrate":
        args.calibrate_args = extra
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    try:
        code: int = args.func(args)
    except (ConfigError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    return code
