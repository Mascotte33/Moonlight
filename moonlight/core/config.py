"""Central bot configuration (T40).

:class:`BotConfig` aggregates everything needed to build the bot: the
calibration profile name, the serial port, the loop parameters and the
per-module decision configs. The module
configs are the existing dataclasses from feature packages (and
:class:`moonlight.core.loop.LoopConfig`); nothing is re-invented here.

A module is **enabled** when its config is not ``None``.

JSON format (``moonlight_config.json``)::

    {
      "profile": "moonlight_user",         # default profile (F-11)
      "serial_port": null,                 # null = auto-detect the Arduino
      "serial_baudrate": 115200,
      "simulation": false,                 # true = FakeInput, no Arduino (F-13)
      "log_commands": false,               # true = every input command goes to the GUI log
      "window_title": "Tibia",             # game window to capture (exact or prefix)
      "profiles_dir": null,                # null = moonlight/profiles
      "templates_dir": "data/templates",   # cursor/*.png, loot/*.png
      "routes_dir": "data/waypoints",
      "loop": {"tick_rate": 20.0},         # any LoopConfig field
      "clicker": {"open_loop": false},     # any ClickerConfig field (F-14)
      "monster_name_source": false,        # unused
      "capacity_source": false,            # unused
      "modules": {
        "healing":   {"enabled": true, "potion_cooldown_ms": 1000},
        "targeting": {"enabled": true},
        "alarms":    {"low_hp_threshold": 20},  # no switch, like extras
        "cavebot":   {"enabled": false, "route": "rotworms"},
        "looting":   {"enabled": false},
        "refill":    {"enabled": false},  # follows cavebot; 0 = off
        "scripts":   {"enabled": false, "timers": "eat 60"},
        "extras":    {"food": true, "food_hotkey": "F10"},  # no switch: a ticked extra is on
        "rotation":  {"enabled": false, "logout_hotkey": "ctrl+l", "accounts": [
          {"login": "abc", "password": "xyz", "character": 1, "minutes": 60}
        ]}
      }
    }

Fail closed: ``profile``, ``modules`` and every module's ``enabled`` flag
are required; unknown keys, wrong types, invalid values, a bad hotkey
combo or a missing cavebot route file all raise :class:`ConfigError`.
Fields left out of a module section take the dataclass defaults.

A module that is off keeps its values: ``{"enabled": false, "potion_cooldown_ms":
800}`` is parsed into :attr:`BotConfig.off` and written back the same way, so
turning the module on again restores them. The bot never reads ``off``.

The default profile is ``moonlight_user`` (F-11); the GUI and
``tools/check_setup.py`` use it when no profile is given.
"""

from __future__ import annotations

import json
import types
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from moonlight.alarms.alarms import AlarmConfig
from moonlight.cavebot.cavebot import Route, RouteError, RouteFileLoader
from moonlight.core.loop import GameLoop, LoopConfig
from moonlight.extras.extras import ExtrasConfig
from moonlight.hardware.clicker import ClickerConfig
from moonlight.healing.healing import HealingConfig
from moonlight.looting.looting import LootingConfig
from moonlight.refill.refill import RefillConfig
from moonlight.scripts.rotation import RotationConfig
from moonlight.scripts.scripts import ScriptsConfig
from moonlight.targeting.targeting import TargetingConfig

__all__ = [
    "ALWAYS_ON",
    "DEFAULT_CONFIG_FILE",
    "MODULES",
    "BotConfig",
    "CavebotConfig",
    "ConfigError",
]

#: Default config file name (looked up in the current directory).
DEFAULT_CONFIG_FILE = "moonlight_config.json"

#: Modules with no switch: always built, no ``enabled`` key (a missing section = defaults).
ALWAYS_ON = ("alarms", "extras")

#: Module section names, in ``modules`` of the JSON file.
MODULES = (
    "healing",
    "targeting",
    "alarms",
    "cavebot",
    "looting",
    "refill",
    "scripts",
    "extras",
    "rotation",
)

_TOP_KEYS = {
    "profile",
    "serial_port",
    "serial_baudrate",
    "simulation",
    "log_commands",
    "walk_keys",
    "window_title",
    "profiles_dir",
    "templates_dir",
    "routes_dir",
    "monster_name_source",
    "capacity_source",
    "loop",
    "clicker",
    "modules",
}


class ConfigError(ValueError):
    """The bot config is missing, unreadable or invalid."""


@dataclass(frozen=True)
class CavebotConfig:
    """Cavebot settings: the loaded :class:`Route` plus its tool hotkeys."""

    route: Route
    rope_hotkey: str = "F9"
    shovel_hotkey: str = "F10"
    #: 0-based waypoint the route starts at.
    start_index: int = 0


@dataclass(frozen=True)
class BotConfig:
    """Everything needed to build and run the bot (see the module docstring)."""

    profile: str
    serial_port: str | None = None
    serial_baudrate: int = 115200
    #: F-13: true runs on a FakeInput instead of the Arduino. A missing
    #: Arduino with this off is a hard error, never a silent fallback.
    simulation: bool = False
    log_commands: bool = False
    #: The cavebot walks with arrow keys along a path instead of clicking the minimap.
    walk_keys: bool = False
    #: Same as ``perception.capture.TIBIA_WINDOW_TITLE``; not imported, that pulls in mss.
    window_title: str = "Tibia"
    profiles_dir: Path | None = None
    templates_dir: Path = Path("data/templates")
    routes_dir: Path = Path("data/waypoints")
    loop: LoopConfig = LoopConfig()
    #: Clicker mode (F-14): closed loop by default; open loop if T33 HIL
    #: shows the rendered cursor cannot be captured.
    clicker: ClickerConfig = ClickerConfig()
    healing: HealingConfig | None = None
    targeting: TargetingConfig | None = None
    #: HP alarms; always built.
    alarms: AlarmConfig = AlarmConfig()
    cavebot: CavebotConfig | None = None
    looting: LootingConfig | None = None
    refill: RefillConfig | None = None
    #: Timed scripts; on or off, ``script`` waypoints always run.
    scripts: ScriptsConfig | None = None
    #: Food, haste, buff potion, ring, amulet, player timer, PM alarm; always built.
    extras: ExtrasConfig = ExtrasConfig()
    #: Accounts played in turn; their logins stay in this gitignored file.
    rotation: RotationConfig | None = None
    #: Unused; kept so saved configs still load (refill without a capacity
    #: reader triggers on Refill now only).
    monster_name_source: bool = False
    capacity_source: bool = False
    #: Kept values of modules that are off, by module name (T0.F1.K4).
    off: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default(cls) -> BotConfig:
        """Safe defaults: healing and targeting on; the rest off."""
        cfg = cls(
            profile="moonlight_user",
            healing=HealingConfig(),
            targeting=TargetingConfig(),
        )
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> BotConfig:
        """Load and validate a config file.

        Raises:
            ConfigError: If the file is missing, not JSON, or invalid.
        """
        p = Path(path)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read config {p}: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: object) -> BotConfig:
        """Build and validate a config from decoded JSON.

        Raises:
            ConfigError: On any missing, unknown or malformed field.
        """
        data = dict(_require_dict(raw, "config"))
        data.pop("minimap_start", None)  # old files: the map gives the position now
        _reject_unknown(data, _TOP_KEYS, "config")
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        if "profile" not in data:
            raise ConfigError("config: missing required key 'profile'")
        for key in (
            "profile",
            "serial_port",
            "serial_baudrate",
            "simulation",
            "log_commands",
            "walk_keys",
            "window_title",
            "profiles_dir",
            "templates_dir",
            "routes_dir",
            "monster_name_source",
            "capacity_source",
        ):
            if key in data:
                kwargs[key] = _coerce(data[key], hints[key], key)
        if not kwargs["profile"]:
            raise ConfigError("profile must be a non-empty string")
        if "loop" in data:
            kwargs["loop"] = _build(LoopConfig, data["loop"], "loop")
        if "clicker" in data:
            kwargs["clicker"] = _build(ClickerConfig, data["clicker"], "clicker")

        modules = _require_dict(data.get("modules"), "modules")
        _reject_unknown(modules, set(MODULES), "modules")
        routes_dir = kwargs.get("routes_dir", Path("data/waypoints"))
        for name in MODULES:
            if name in ("scripts", "alarms", "extras", "rotation") and name not in modules:
                continue  # configs saved before these had a section
            if name not in modules:
                raise ConfigError(f"modules: missing required section {name!r}")
            section = dict(_require_dict(modules[name], f"modules.{name}"))
            enabled = section.pop("enabled", None)
            if name in ALWAYS_ON:  # old files: the switch is gone
                enabled = True
            elif not isinstance(enabled, bool):
                raise ConfigError(f"modules.{name}.enabled must be true or false")
            if name == "refill":  # no switch: on with the cavebot, its 0s turn it off
                cavebot = _require_dict(modules.get("cavebot"), "modules.cavebot")
                enabled = cavebot.get("enabled") is True
            if not enabled and not section:
                continue
            if name == "cavebot":
                old = section.pop("walk_keys", None)  # lived here before it moved to Settings
                if isinstance(old, bool):
                    kwargs.setdefault("walk_keys", old)
                value = _cavebot(section, routes_dir)
            else:
                if name == "looting":  # keys of removed loot rules
                    for key in ("move_all_hotkey", "max_items_per_loot", "min_cap_to_loot"):
                        section.pop(key, None)
                if name == "refill":  # the refill route file's keys, gone with it
                    for key in (
                        "stop_cap", "route_name", "storage_hotkey", "deposit_hotkey", "routes_dir",
                        "trigger_cap",  # used cap %, now the free cap number cap_below
                    ):
                        section.pop(key, None)
                value = _coerce(section, hints[name], f"modules.{name}")
            if enabled:
                kwargs[name] = value
            else:
                kwargs.setdefault("off", {})[name] = value

        cfg = cls(**kwargs)
        cfg.validate()
        return cfg

    def to_dict(self) -> dict[str, Any]:
        """The JSON :meth:`from_dict` reads; cavebot is saved by its route name."""
        data: dict[str, Any] = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in MODULES and f.name != "off"
        }
        data["loop"] = asdict(self.loop)
        data["clicker"] = asdict(self.clicker)
        modules: dict[str, Any] = {}
        for name in MODULES:
            cfg = getattr(self, name)
            kept = self.off.get(name)
            if name in ALWAYS_ON:
                modules[name] = _section(cfg)
            elif cfg is not None:
                modules[name] = {"enabled": True, **_section(cfg)}
            elif kept is not None:
                modules[name] = {"enabled": False, **_section(kept)}
            else:
                modules[name] = {"enabled": False}
        data["modules"] = modules
        # Round-trip through JSON so Paths become str and tuples become lists.
        result: dict[str, Any] = json.loads(json.dumps(data, default=str))
        return result

    def save(self, path: str | Path) -> None:
        """Write :meth:`to_dict` to *path* as JSON."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def validate(self) -> BotConfig:
        """Cross-field checks; return self.

        Raises:
            ConfigError: If a value or module combination is invalid.
        """
        for name in self.off:
            if name not in MODULES or getattr(self, name) is not None:
                raise ConfigError(f"off: {name!r} is not a module that is off")
        try:
            GameLoop._validate_config(self.loop)
            sections: tuple[Any, ...] = (
                self.healing,
                self.targeting,
                self.alarms,
                self.scripts,
                self.extras,
                self.rotation,
            )
            for cfg in sections:
                if cfg is not None:
                    cfg.validate()
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        if self.cavebot is not None:
            n = len(self.cavebot.route.waypoints)
            if not 0 <= self.cavebot.start_index < n:
                raise ConfigError(
                    f"cavebot start waypoint {self.cavebot.start_index + 1} is outside 1..{n}"
                )
        if self.refill is not None and self.cavebot is None:
            raise ConfigError("refill needs the cavebot module enabled")
        if self.rotation is not None and self.cavebot is None:
            raise ConfigError("rotation needs the cavebot module enabled")
        if self.rotation is not None and len(self.rotation.accounts) < 2:
            # checked only when on, so the Settings tab can save the logout hotkey
            raise ConfigError("rotation needs at least 2 accounts")
        return self


# -- parsing helpers ----------------------------------------------------------


def _require_dict(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be a JSON object")
    return value


def _reject_unknown(data: dict[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}")


def _section(cfg: Any) -> dict[str, Any]:
    """A module config as its JSON section, without ``enabled``."""
    if isinstance(cfg, CavebotConfig):
        return {
            "route": cfg.route.name,
            "rope_hotkey": cfg.rope_hotkey,
            "shovel_hotkey": cfg.shovel_hotkey,
            "start_index": cfg.start_index,
        }
    return asdict(cfg)


def _cavebot(section: dict[str, Any], routes_dir: Path) -> CavebotConfig:
    name = section.pop("route", None)
    if not isinstance(name, str) or not name:
        raise ConfigError("modules.cavebot.route must be a non-empty route name")
    try:
        route = RouteFileLoader.load(routes_dir / f"{name}.json")
    except RouteError as exc:
        raise ConfigError(f"modules.cavebot: {exc}") from exc
    kinds = {"start_index": int, "rope_hotkey": str, "shovel_hotkey": str}
    _reject_unknown(section, set(kinds), "modules.cavebot")
    keys = {k: _coerce(v, kinds[k], f"modules.cavebot.{k}") for k, v in section.items()}
    return CavebotConfig(route=route, **keys)


def _build(cls: Any, raw: object, where: str) -> Any:
    """Instantiate dataclass *cls* from *raw*, type-checking every given field."""
    data = _require_dict(raw, where)
    names = {f.name for f in fields(cls)}
    _reject_unknown(data, names, where)
    hints = get_type_hints(cls)
    kwargs = {k: _coerce(v, hints[k], f"{where}.{k}") for k, v in data.items()}
    try:
        obj = cls(**kwargs)
        validate = getattr(obj, "validate", None)
        if validate is not None:
            validate()
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: {exc}") from exc
    return obj


def _coerce(value: object, hint: Any, where: str) -> Any:
    """Check *value* against type *hint* (JSON -> Python), or raise ConfigError."""
    origin = get_origin(hint)
    args = get_args(hint)
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        (inner,) = [a for a in args if a is not type(None)]
        return _coerce(value, inner, where)
    if hint is float and isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if hint in (int, str, bool):
        if isinstance(value, hint) and (hint is bool or not isinstance(value, bool)):
            return value
    elif hint is Path and isinstance(value, str):
        return Path(value)
    elif origin in (list, tuple) and isinstance(value, list):
        items = [_coerce(v, args[0], f"{where}[{i}]") for i, v in enumerate(value)]
        return items if origin is list else tuple(items)
    elif origin is dict and isinstance(value, dict):
        return {
            _coerce(k, args[0], where): _coerce(v, args[1], f"{where}.{k}")
            for k, v in value.items()
        }
    elif is_dataclass(hint) and isinstance(value, dict):
        return _build(hint, value, where)
    raise ConfigError(f"{where}: expected {getattr(hint, '__name__', hint)}, got {value!r}")
