"""Tab base class and small shared widget helpers (T39).

Every tab:

* receives a :class:`moonlight.gui.state_bridge.GuiContext` in its
  constructor (dependency injection — no globals, no display assumptions),
* builds its widgets in :meth:`build` (called once by the app shell),
* updates them in :meth:`on_poll` (called on every state poll, ~10-20 Hz),
* lists its config options in ``options`` (Rule 2 of GUI_REMAKE.MD):
  :meth:`build_options` makes one control per :class:`Option` and
  :meth:`apply` writes them all into ``moonlight_config.json``.

``on_poll`` must stay fast (label/progress-bar updates only) — it runs on
the Tk thread while the bot loop thread keeps ticking in the background.
"""

from __future__ import annotations

import time
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.setup.config import MODULES, BotConfig, ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState
    from moonlight.gui.state_bridge import GuiContext

__all__ = [
    "Option",
    "Tab",
    "dot_color",
    "health",
    "read_option", "section",
    "stat_label",
    "stat_num",
    "typed_value",
    "write_option",
]


@dataclass(frozen=True)
class Option:
    """One config option shown as one control.

    *field* is a dotted path into :class:`BotConfig`, e.g.
    ``"healing.normal_hp_percent"``. ``"<module>.enabled"`` turns a module
    on or off; writing any other field never changes that.
    """

    label: str
    field: str
    control: Literal["slider", "switch", "entry", "dropdown"]
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None


def _unwrap(hint: Any) -> Any:
    if get_origin(hint) in (Union, types.UnionType):
        (inner,) = [a for a in get_args(hint) if a is not type(None)]
        return inner
    return hint


def _field_type(path: list[str]) -> Any:
    hint: Any = BotConfig
    for name in path:
        try:
            hint = _unwrap(get_type_hints(hint)[name])
        except (KeyError, TypeError):
            raise ConfigError(f"unknown option field {'.'.join(path)!r}") from None
    return hint


def _allows_none(path: list[str]) -> bool:
    raw = get_type_hints(_field_type(path[:-1]))[path[-1]]
    return type(None) in get_args(raw)


def _cast(value: object, hint: Any, field: str) -> object:
    """Fit a control's value to the field's type (sliders give floats)."""
    try:
        if hint is int and isinstance(value, float):
            return round(value)
        if hint in (int, float) and isinstance(value, str):
            return hint(value.strip())
    except ValueError:
        raise ConfigError(f"{field}: {value!r} is not a number") from None
    return value


def read_option(cfg: BotConfig, field: str) -> object:
    """The value *field* has in *cfg*, using a turned-off module's kept values.

    An off module with no kept values shows its defaults; ``None`` when it
    has none (cavebot needs a route).
    """
    head, *rest = field.split(".")
    if head in MODULES and rest == ["enabled"]:
        return getattr(cfg, head) is not None
    _field_type([head, *rest])
    node: Any = cfg
    if head in MODULES:
        node = getattr(cfg, head) or cfg.off.get(head)
        if node is None:
            try:
                node = _field_type([head])()
            except TypeError:
                return None
    else:
        rest = [head, *rest]
    for name in rest:
        node = getattr(node, name)
    return node


def write_option(cfg: BotConfig, option: Option, value: object) -> BotConfig:
    """A new config with *value* written into *option*'s field.

    Goes through :meth:`BotConfig.from_dict`, so the result is checked like a
    loaded file. A turned-off module keeps the value in ``BotConfig.off``.

    Raises:
        ConfigError: For an unknown field, a value outside *option*'s range,
            or a config that fails validation.
    """
    path = option.field.split(".")
    head = path[0]
    if value == "" and _allows_none(path):
        value = None
    elif not (head in MODULES and path[1:] == ["enabled"]):
        value = _cast(value, _field_type(path), option.field)
    if isinstance(value, int | float) and not isinstance(value, bool):
        low, high = option.min, option.max
        if (low is not None and value < low) or (high is not None and value > high):
            raise ConfigError(f"{option.field}: {value} is outside {low}..{high}")
    data = cfg.to_dict()
    node: dict[str, Any] = data["modules"] if head in MODULES else data
    for name in path[:-1]:
        node = node[name]
    node[path[-1]] = value
    return BotConfig.from_dict(data)


def section(parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
    """A titled, inset frame (consistent look across tabs)."""
    box = ctk.CTkFrame(
        parent,
        fg_color=theme.PANEL,
        border_width=1,
        border_color=theme.RAISED,
        corner_radius=theme.RADIUS_CARD,
    )
    ctk.CTkLabel(
        box,
        text=title.upper(),
        anchor="w",
        font=ctk.CTkFont(size=theme.SIZE_TITLE, weight="bold"),
        text_color=theme.TEXT_MUTED,
    ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
    return box


def stat_label(parent: ctk.CTkFrame, title: str) -> ctk.CTkLabel:
    """A right-aligned stat label (value updated in on_poll)."""
    return ctk.CTkLabel(
        parent,
        text=f"{title}: --",
        anchor="w",
        font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_BODY),
    )


def stat_num(stats: dict[str, object], key: str, default: float = 0.0) -> float:
    """Read a numeric field from a loop-stats dict (values are typed object)."""
    value = stats.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def health(
    snap: WorldState, arduino: bool, running: bool, now: float | None = None
) -> list[tuple[str, bool]]:
    """``(name, ok)`` per subsystem the bot can report today.

    Vitals count as ok while ``last_update`` is under 2 s old.
    """
    now = time.monotonic() if now is None else now
    pos = snap.position
    return [
        ("Screen", snap.screen_ok),
        ("Vitals", snap.last_update > 0 and now - snap.last_update < 2.0),
        ("Minimap", (pos.x, pos.y, pos.z) != (0, 0, 0)),
        ("Arduino", arduino),
        ("Bot", running),
    ]


def dot_color(ok: bool) -> str:
    """Status dot: green when ok, red when not (Look: failed dot)."""
    return theme.LIFE if ok else theme.BLOOD


class Tab:
    """Base class for all 10 GUI tabs.

    Subclasses set :attr:`title` and implement :meth:`build`;
    :meth:`on_poll` is a no-op by default.
    """

    title: str = "Tab"
    #: The tab's config options; a tab file sets this to its ``OPTIONS``.
    options: ClassVar[Sequence[Option]] = ()

    def __init__(self, ctx: GuiContext) -> None:
        self.ctx = ctx
        self._readers: dict[str, Callable[[], object]] = {}
        self._writers: dict[str, Callable[[object], None]] = {}

    def build(self, parent: ctk.CTkFrame) -> None:
        """Create this tab's widgets inside *parent* (called once)."""
        raise NotImplementedError

    def on_poll(self, snap: WorldState) -> None:
        """Update widgets from a state snapshot (default: no-op)."""
        return None

    def build_options(self, parent: ctk.CTkFrame, options: list[Option] | None = None) -> None:
        """One labelled control per entry in *options* (default ``options``)."""
        if self.options and self.apply not in self.ctx.config_savers:
            self.ctx.config_listeners.append(self.reload_options)
            self.ctx.config_savers.append(self.apply)
        cfg = load_real_config()
        for option in self.options if options is None else options:
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
            ctk.CTkLabel(
                row,
                text=option.label,
                anchor="w",
                font=ctk.CTkFont(size=theme.SIZE_BODY),
                text_color=theme.TEXT_DIM,
            ).pack(side="left")
            get, put = _control(row, option)
            put(read_option(cfg, option.field))
            self._readers[option.field] = get
            self._writers[option.field] = put

    def reload_options(self) -> None:
        """Refill every control from the picked config."""
        cfg = load_real_config()
        for option in self.options:
            if option.field in self._writers:
                self._writers[option.field](read_option(cfg, option.field))

    def apply(self) -> BotConfig:
        """Write every option into the picked config and save it (Rule 2).

        Raises:
            ConfigError: When a value is refused; nothing is saved then.
        """
        path = picked_config()
        cfg = load_real_config(path)
        for option in self.options:
            if option.field in self._readers:
                cfg = write_option(cfg, option, self._readers[option.field]())
        cfg.save(path)
        return cfg


def typed_value(text: str, low: int, high: int, fallback: int) -> int:
    """*text* as a whole number inside low..high, or *fallback* if it isn't one."""
    try:
        value = round(float(text))
    except ValueError:
        return fallback
    return min(max(value, low), high)


def _control(
    row: ctk.CTkFrame, option: Option
) -> tuple[Callable[[], object], Callable[[object], None]]:
    """Pack *option*'s control into *row*; return functions to read and set it."""
    if option.control == "switch":
        switch = ctk.CTkSwitch(row, text="", progress_color=theme.LIFE)
        switch.pack(side="right")
        return lambda: bool(switch.get()), lambda v: switch.select() if v else switch.deselect()
    if option.control == "dropdown":
        choices = option.choices or []
        menu = ctk.CTkOptionMenu(
            row,
            values=choices,
            fg_color=theme.RAISED,
            button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
        )
        menu.pack(side="right")
        fallback = choices[0] if choices else ""
        return lambda: menu.get(), lambda v: menu.set(fallback if v is None else str(v))
    if option.control == "slider":
        low = int(option.min if option.min is not None else 0)
        high = int(option.max if option.max is not None else 100)
        box = ctk.CTkEntry(
            row,
            width=56,
            justify="right",
            font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_BODY),
            fg_color=theme.NIGHT,
            border_color=theme.RAISED,
        )

        def show(value: float) -> None:
            box.delete(0, "end")
            box.insert(0, str(round(value)))

        slider = ctk.CTkSlider(
            row,
            from_=low,
            to=high,
            number_of_steps=max(high - low, 1),
            button_color=theme.MANA,
            command=show,
        )
        box.pack(side="right")
        slider.pack(side="right", padx=theme.UNIT)

        def get_slider() -> int:
            value = typed_value(box.get(), low, high, round(slider.get()))
            slider.set(value)
            show(value)
            return value

        def put_slider(value: object) -> None:
            start = round(value) if isinstance(value, int | float) else low
            slider.set(start)
            show(start)

        for event in ("<Return>", "<FocusOut>"):
            box.bind(event, lambda _e: get_slider())
        return get_slider, put_slider
    entry = ctk.CTkEntry(row, fg_color=theme.NIGHT, border_color=theme.RAISED)
    entry.pack(side="right")

    def put_entry(value: object) -> None:
        entry.delete(0, "end")
        entry.insert(0, "" if value is None else str(value))

    return lambda: entry.get(), put_entry
