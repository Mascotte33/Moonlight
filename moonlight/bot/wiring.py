"""Bot assembly (T40): the one place the bot loop is built.

:func:`build_loop` is the integration seam: it assembles perception ->
WorldState -> decision modules -> :class:`GameLoop` from a
:class:`BotConfig`, a :class:`CalibrationProfile`, any
:class:`InputBackend` and any frame provider -- so a headless test can
drive the real wiring with ``FakeInput`` and synthetic frames.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from moonlight.bot.loop import DecisionModules, GameLoop
from moonlight.bot.safety import RateLimitedInput, beep_alert
from moonlight.bot.state import WorldState
from moonlight.hardware.input import InputBackend
from moonlight.setup.calibrate import CalibrationProfile
from moonlight.setup.config import MODULES, BotConfig

if TYPE_CHECKING:
    from moonlight.hardware.clicker import ClickController
    from moonlight.perception.capture import ScreenCapture

__all__ = [
    "MODULE_REGIONS",
    "FrameCache",
    "TickGrabber",
    "build_loop",
    "capture_providers",
    "capture_regions",
    "check_module_regions",
    "make_perceive",
]

FrameProvider = Callable[[], np.ndarray]
Rect = tuple[int, int, int, int]

logger = logging.getLogger(__name__)

#: Calibration regions each enabled decision module needs, derived from the
#: perception readers in :func:`make_perceive` / :func:`build_loop`.
#: ``tools/check_setup.py`` uses this to report per-module readiness (F-11): a
#: module is MISSING when any of its required regions is absent from the
#: profile. ``looting`` and ``refill`` reference regions that are not part of
#: the standard calibration set (they need dedicated readers, T34), so they
#: report as MISSING until those are calibrated.
MODULE_REGIONS: dict[str, tuple[str, ...]] = {
    "healing": ("hp_bar", "mp_bar"),
    "targeting": ("battle_list",),
    "attack_spells": ("battle_list", "mp_bar"),
    "cavebot": ("minimap", "floor_indicator", "game_grid"),
    "looting": ("loot_window",),
    "refill": ("capacity",),
    "buffs": ("status_icons",),
    "alarms": (),
}


#: Regions :func:`make_perceive` reads every tick.
_PERCEIVE_REGIONS = ("hp_bar", "mp_bar", "battle_list", "minimap", "floor_indicator")


def capture_regions(profile: CalibrationProfile, config: BotConfig) -> list[Rect] | None:
    """The window regions a tick reads, for ``ScreenCapture.get_frame`` (P2).

    ``None`` (grab the full window) when any of them is not calibrated.
    ``game_grid`` is left out: it is only click geometry, and the clicker
    grabs its own small cursor ROI.
    """
    names = set(_PERCEIVE_REGIONS)
    for module in _enabled_modules(config):
        names.update(MODULE_REGIONS.get(module, ()))
    names.discard("game_grid")
    if any(name not in profile.regions for name in names):
        return None
    return [profile.get_region(name).to_tuple for name in sorted(names)]


class FrameCache:
    """One capture per tick (P1).

    The perceive step calls :meth:`refresh` once per tick; every other
    consumer in the same tick (clicker, looting) calls the cache and gets
    that same frame instead of capturing again.
    """

    def __init__(self, provider: FrameProvider) -> None:
        self._provider = provider
        self._frame: np.ndarray | None = None

    def refresh(self) -> np.ndarray:
        """Capture a new frame (once per tick)."""
        self._frame = self._provider()
        return self._frame

    def __call__(self) -> np.ndarray:
        """This tick's frame (captured now if none was yet)."""
        return self._frame if self._frame is not None else self.refresh()


class TickGrabber:
    """Production capture: ONE screen grab per tick (P2 + P5).

    Every mss grab costs a ~17 ms fixed sync whatever its size, so a
    separate cursor-ROI grab would double a clicking tick. The tick grab
    therefore also covers the region the clicker's next step reads
    (:meth:`ClickController.next_read`), taken no sooner than
    ``settle_ms`` after its last move; :meth:`roi` then serves that frame
    and only grabs again when the region was not covered.
    """

    def __init__(
        self,
        capture: ScreenCapture,
        regions: list[Rect] | None,
        settle_ms: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._capture = capture
        self._regions = regions
        self._settle = settle_ms / 1000.0
        self._clock = clock
        self._sleep = sleep_fn
        self._next_read: Callable[[], tuple[Rect, float] | None] | None = None
        self._frame: np.ndarray | None = None
        self._covered: Rect | None = None

    def attach(self, clicker: ClickController) -> None:
        """Fold *clicker*'s next cursor read into each tick grab."""
        self._next_read = clicker.next_read

    def frame(self) -> np.ndarray:
        """The tick grab: the tick regions plus the clicker's next read."""
        read = self._next_read() if self._next_read is not None else None
        self._covered = None
        regions = self._regions
        if read is not None:
            region, last_move = read
            wait = last_move + self._settle - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._covered = region
            if regions is not None:
                regions = [*regions, region]
        self._frame = self._capture.get_frame(regions)
        return self._frame

    def roi(self, region: Rect) -> np.ndarray:
        """The clicker's cursor read: this tick's frame when it covers it."""
        if region == self._covered and self._frame is not None:
            self._covered = None  # a second read in the same tick grabs fresh
            return self._frame
        return self._capture.get_frame([region])


def _enabled_modules(config: BotConfig) -> list[str]:
    """Names of the decision modules enabled in *config*, in MODULES order."""
    return [name for name in MODULES if getattr(config, name) is not None]


def check_module_regions(
    profile: CalibrationProfile, config: BotConfig
) -> dict[str, list[str]]:
    """Missing calibration regions per enabled module (F-11).

    Returns a mapping from each enabled module name to the list of its
    required regions absent from *profile* (empty when the module is ready).
    Only enabled modules appear in the result.
    """
    report: dict[str, list[str]] = {}
    for name in _enabled_modules(config):
        required = MODULE_REGIONS.get(name, ())
        report[name] = [r for r in required if r not in profile.regions]
    return report

# -- integration seam ---------------------------------------------------------


def start_position(config: BotConfig) -> tuple[int, int, int] | None:
    """Where the minimap tracker starts: ``minimap_start``, else the route's first waypoint."""
    if config.minimap_start is not None:
        return config.minimap_start
    if config.cavebot is not None and config.cavebot.route.waypoints:
        wp = config.cavebot.route.waypoints[0]
        return (wp.x, wp.y, wp.z)
    return None


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
    from moonlight.bot.state import Position
    from moonlight.perception.battle import BattleReader, entries_to_state
    from moonlight.perception.floor import FloorReader
    from moonlight.perception.minimap import MinimapReader
    from moonlight.perception.vitals import VitalsReader

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
            if minimap.lost:
                # F-17: unknown until re-anchored; the cavebot reads (0,0,0)
                # as "no position" and halts "stuck" instead of walking blind.
                state.position = Position(0, 0, 0)
            else:
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
    roi_provider: Callable[[Rect], np.ndarray] | None = None,
    window_origin: Callable[[], tuple[int, int]] | None = None,
    on_clicker: Callable[[ClickController], None] | None = None,
) -> GameLoop:
    """Assemble the bot: perception + the enabled decision modules + loop.

    *frame_provider* is called exactly once per tick (P1, via
    :class:`FrameCache`). *roi_provider* / *window_origin* feed the
    clicker's small cursor-ROI grabs and open-loop mode (F-14); without
    them the clicker reads the cached tick frame. *on_clicker* receives
    the click controller once built (:class:`TickGrabber.attach`).

    Raises:
        ValueError: If an enabled module cannot be built from *profile*
            (e.g. cavebot without a minimap/game_grid region).
    """
    state = state if state is not None else WorldState()
    frames = FrameCache(frame_provider)
    # F-20: one wire limiter shared by the loop and every module that sends
    # (clicker, cavebot, looting, refill), so the 10/s cap counts serial
    # commands, not actions.
    backend = RateLimitedInput.wrap(backend, config.loop.max_actions_per_second, clock)

    def modules(cfg: BotConfig, old: tuple[BotConfig, DecisionModules] | None) -> DecisionModules:
        return build_modules(
            cfg, profile, backend, state, frames, old=old, clock=clock,
            roi_provider=roi_provider, window_origin=window_origin, on_clicker=on_clicker,
        )

    current = config
    m = modules(config, None)
    loop = GameLoop(
        state,
        backend,
        config.loop,
        modules=m,
        perceive=make_perceive(
            state,
            profile,
            frames.refresh,
            focus_check,
            minimap_start=start_position(config),
        ),
        clock=clock,
        sleep_fn=sleep_fn,
        alert=alert,
    )

    def reconfigure(new: BotConfig) -> bool:
        nonlocal current
        if needs_restart(current, new):
            return False
        loop.modules = modules(new, (current, loop.modules))
        current = new
        return True

    loop.reconfigure = reconfigure
    return loop


#: BotConfig fields a running bot takes without a rebuild (live apply).
LIVE_FIELDS = (*MODULES, "hotkeys", "clicker", "templates_dir", "routes_dir", "off")


def needs_restart(old: BotConfig, new: BotConfig) -> bool:
    """True when *new* changes something only a fresh bot can take.

    That is any field outside :data:`LIVE_FIELDS` (port, window, tick
    rate...), or a module switched on or off, since the captured regions
    depend on which modules run.
    """
    # doc: docs/prs/gui-calibration.md#live-apply
    names = [f.name for f in fields(old) if f.name not in LIVE_FIELDS]
    if any(getattr(old, n) != getattr(new, n) for n in names):
        return True
    return any((getattr(old, n) is None) != (getattr(new, n) is None) for n in MODULES)


def build_modules(
    config: BotConfig,
    profile: CalibrationProfile,
    backend: InputBackend,
    state: WorldState,
    frames: FrameCache,
    *,
    old: tuple[BotConfig, DecisionModules] | None = None,
    clock: Callable[[], float] | None = None,
    roi_provider: Callable[[Rect], np.ndarray] | None = None,
    window_origin: Callable[[], tuple[int, int]] | None = None,
    on_clicker: Callable[[ClickController], None] | None = None,
) -> DecisionModules:
    """The enabled decision modules for *config*.

    With *old* (the running config and its modules) a module whose settings
    did not change is kept as is, so its cooldowns and route position
    survive; a rebuilt cavebot walks to the nearest waypoint of its route.
    """
    from moonlight.decision.alarms import AlarmDecision
    from moonlight.decision.attack_spells import AttackSpells
    from moonlight.decision.buffs import BuffManager
    from moonlight.decision.healing import HealingDecision
    from moonlight.decision.targeting import TargetingDecision

    combos = config.spell_combos
    m = DecisionModules()
    prev_cfg, prev = old if old is not None else (None, None)

    def same(*names: str) -> bool:
        return prev_cfg is not None and all(
            getattr(prev_cfg, n) == getattr(config, n) for n in names
        )

    if prev is not None:
        for name, keys in (
            ("healing", ("healing", "hotkeys")),
            ("targeting", ("targeting",)),
            ("attack_spells", ("attack_spells", "hotkeys")),
            ("buffs", ("buffs", "hotkeys")),
            ("alarms", ("alarms",)),
        ):
            if same(*keys):
                setattr(m, name, getattr(prev, name))
        if same("cavebot", "looting", "refill", "clicker", "templates_dir", "routes_dir"):
            m.cavebot, m.looting, m.refill = prev.cavebot, prev.looting, prev.refill
    if config.healing is not None and m.healing is None:
        m.healing = HealingDecision(config.healing, spell_combos=combos, clock=clock)
    if config.targeting is not None and m.targeting is None:
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
    if config.attack_spells is not None and m.attack_spells is None:
        m.attack_spells = AttackSpells(config.attack_spells, combos, clock=clock)
    if config.buffs is not None and m.buffs is None:
        m.buffs = BuffManager(config.buffs, spell_combos=combos, clock=clock)
    if config.alarms is not None and m.alarms is None:
        m.alarms = AlarmDecision(config.alarms, clock=clock)

    walkers = config.cavebot is not None or config.looting is not None
    if walkers and m.cavebot is None and m.looting is None:
        from moonlight.hardware.clicker import ClickController
        from moonlight.perception.cursor import CursorReader

        cursor = list(_templates(config.templates_dir / "cursor").values())
        clicker = ClickController(
            backend,
            CursorReader(profile, cursor_templates=cursor or None),
            frames,
            config=config.clicker,
            roi_provider=roi_provider,
            window_origin=window_origin,
        )
        if on_clicker is not None:
            on_clicker(clicker)
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
            if prev is None:
                m.cavebot.start()
                m.cavebot.route.current = cb.start_index
            else:
                m.cavebot.start(nearest=True)
        if config.looting is not None:
            from moonlight.decision.looting import Looting
            from moonlight.perception.loot import LootReader

            reader = LootReader(_templates(config.templates_dir / "loot"), profile)
            m.looting = Looting(
                reader, clicker, backend, state, config.looting, frame_provider=frames
            )
    if config.refill is not None and m.refill is None:
        from moonlight.decision.cavebot import RouteFileLoader
        from moonlight.decision.refill import Refill

        assert m.cavebot is not None and m.looting is not None  # BotConfig.validate
        m.refill = Refill(config.refill, m.cavebot, RouteFileLoader(), m.looting, state, backend)

    return m


def capture_providers(
    capture: ScreenCapture, profile: CalibrationProfile, config: BotConfig
) -> dict[str, Any]:
    """:func:`build_loop` keyword arguments backed by a real *capture*."""
    regions = capture_regions(profile, config)
    if regions is None:
        logger.warning("uncalibrated regions: capturing the full window every tick")

    def origin() -> tuple[int, int]:
        rect = capture.get_window_rect()
        return (rect[0], rect[1]) if rect is not None else (0, 0)

    grabber = TickGrabber(capture, regions, config.clicker.settle_ms)
    return {
        "frame_provider": grabber.frame,
        "focus_check": capture.is_focused,
        "roi_provider": grabber.roi,
        "window_origin": origin,
        "on_clicker": grabber.attach,
    }
