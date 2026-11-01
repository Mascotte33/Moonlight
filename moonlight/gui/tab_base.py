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
import tkinter as tk
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

from moonlight.core.config import MODULES, BotConfig, ConfigError
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState
    from moonlight.gui.state_bridge import GuiContext

__all__ = [
    "HotkeyBox",
    "Label",
    "Option",
    "Tab",
    "dot_color",
    "health",
    "read_option", "section",
    "stat_label",
    "stat_num",
    "combo_from_key",
    "typed_value",
    "write_option",
]


@dataclass(frozen=True)
class Option:
    """One config option shown as one control.

    *field* is a dotted path into :class:`BotConfig`, e.g.
    ``"healing.normal_hp_percent"``. ``"<module>.enabled"`` turns a module
    on or off; writing any other field never changes that. An entry with
    *off_at_zero* shows a grayed "off" while it holds 0. An option with an
    empty *label* sits on the row of the option before it.
    """

    label: str
    field: str
    control: Literal["slider", "switch", "checkbox", "entry", "dropdown", "hotkey"]
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None
    off_at_zero: bool = False


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


# doc: docs/prs/gui-smooth.md#light-labels
def mode_color(color: Any) -> str:
    """A CTk color (one color, or a light/dark pair) for the current mode."""
    if isinstance(color, (tuple, list)):
        return str(color[ctk.AppearanceModeTracker.appearance_mode])
    return str(color)


# doc: docs/prs/gui-log.md#plain-layout-frames
class Row(tk.Frame):
    """A layout-only frame: a transparent ``ctk.CTkFrame`` that is one Tk window.

    A CTkFrame is a frame and a canvas, redrawn on every tab switch even when
    it draws nothing; a tab switch costs about 2 ms per native window. This
    one takes its parent's color, so nothing on screen changes.
    """

    _detect_color_of_master = ctk.CTkBaseClass._detect_color_of_master

    def __init__(self, master: Any, **options: Any) -> None:
        super().__init__(master, borderwidth=0, highlightthickness=0, **options)
        self.configure(bg=mode_color(self._detect_color_of_master(master)))


class Label(tk.Label):
    """A text label that looks like ``ctk.CTkLabel`` but is one Tk window.

    A CTkLabel is a frame, a canvas and a label: three native windows to
    paint on every tab switch. This one takes the same options the GUI uses
    (``text``, ``text_color``, ``font``, ``anchor``, ``justify``,
    ``wraplength``), sits on its parent's color like a transparent CTkLabel,
    and is at least 28 px tall with the text centered. Labels with a pixel
    ``width`` or a picture stay CTkLabels.
    """

    #: CTkLabel's default (and minimum) height in px.
    HEIGHT = 28
    #: One transparent 1x1 image per Tk interpreter: with an image, ``height``
    #: counts pixels instead of text lines.
    _blank: ClassVar[dict[object, tk.PhotoImage]] = {}
    _detect_color_of_master = ctk.CTkBaseClass._detect_color_of_master

    def __init__(self, master: Any, **options: Any) -> None:
        blank = Label._blank.get(master.tk)
        if blank is None:
            blank = Label._blank[master.tk] = tk.PhotoImage(master=master, width=1, height=1)
        super().__init__(
            master,
            image=blank,
            compound="center",
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        self._text_color: Any = ctk.ThemeManager.theme["CTkLabel"]["text_color"]
        self._font: Any = ctk.CTkFont()
        self.configure(bg=self._mode_color(self._detect_color_of_master(master)))
        self.configure(**{"text_color": self._text_color, "font": self._font, **options})

    def _mode_color(self, color: Any) -> str:
        return mode_color(color)

    def configure(self, cnf: Any = None, **options: Any) -> Any:
        if cnf is not None or not options:
            return super().configure(cnf, **options)
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        if "text_color" in options:
            self._text_color = options.pop("text_color")
            options["fg"] = self._mode_color(self._text_color)
        if "font" in options:
            self._font = options.pop("font")
            font = self._font
            options["font"] = (
                font.create_scaled_tuple(scaling) if isinstance(font, ctk.CTkFont) else font
            )
        if "wraplength" in options:
            options["wraplength"] = round(options["wraplength"] * scaling)
        refit = {"text", "font", "wraplength"} & options.keys()
        super().configure(**options)
        if refit:
            # Natural height first; a single line is then raised to HEIGHT.
            super().configure(height=0)
            minimum = round(self.HEIGHT * scaling)
            if self.winfo_reqheight() < minimum:
                super().configure(height=minimum)
        return None

    config = configure

    def cget(self, key: str) -> Any:
        if key == "text_color":
            return self._text_color
        if key == "font":
            return self._font
        return super().cget(key)


def section(parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
    """A titled, inset frame (consistent look across tabs)."""
    box = ctk.CTkFrame(
        parent,
        fg_color=theme.PANEL,
        border_width=1,
        border_color=theme.RAISED,
        corner_radius=theme.RADIUS_CARD,
    )
    Label(
        box,
        text=title.upper(),
        anchor="w",
        font=ctk.CTkFont(size=theme.SIZE_TITLE, weight="bold"),
        text_color=theme.TEXT_MUTED,
    ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
    return box


def row_entry(
    parent: ctk.CTkFrame,
    text: str,
    width: int,
    on_submit: Callable[[], None],
    pack_left: bool = True,
) -> ctk.CTkEntry:
    """An entry in a rows editor; Return or leaving it calls *on_submit*.

    It is packed left unless *pack_left* is False (the caller grids it).
    """
    entry = ctk.CTkEntry(parent, width=width, fg_color=theme.NIGHT, border_color=theme.RAISED)
    entry.insert(0, text)
    if pack_left:
        entry.pack(side="left", padx=(theme.UNIT, 0))
    for event in ("<Return>", "<FocusOut>"):
        entry.bind(event, lambda _e: on_submit())
    return entry


def row_button(
    parent: ctk.CTkFrame,
    text: str,
    command: Callable[[], None],
    width: int = 28,
    pack_left: bool = True,
) -> ctk.CTkButton:
    """A small button in a rows editor (move, delete, add).

    It is packed left unless *pack_left* is False (the caller grids it).
    """
    button = ctk.CTkButton(
        parent,
        text=text,
        width=width,
        command=command,
        fg_color=theme.RAISED,
        hover_color=theme.HOVER,
    )
    if pack_left:
        button.pack(side="left", padx=(theme.UNIT, 0))
    return button


def stat_label(parent: ctk.CTkFrame, title: str) -> Label:
    """A right-aligned stat label (value updated in on_poll)."""
    return Label(
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


# A customtkinter configure redraws the widget even when nothing changed, and
# 10 polls a second of that made the GUI stutter on a 2-core laptop.
# doc: docs/prs/gui-smooth.md#redraw-only-on-change
def set_label(
    label: Label | ctk.CTkLabel, text: str | None = None, color: str | None = None
) -> None:
    """Configure *label*'s text and/or text color, only where they changed."""
    if text is not None and label.cget("text") != text:
        label.configure(text=text)
    if color is not None and label.cget("text_color") != color:
        label.configure(text_color=color)


def set_bar(bar: ctk.CTkProgressBar, value: float) -> None:
    """Set *bar* to *value* (clamped to 0-1, to whole percents) when it moved."""
    value = round(min(max(value, 0.0), 1.0), 2)
    if round(bar.get(), 2) != value:
        bar.set(value)


class Tab:
    """Base class for all 10 GUI tabs.

    Subclasses set :attr:`title` and implement :meth:`build`;
    :meth:`on_poll` is a no-op by default.
    """

    title: str = "Tab"
    #: The tab's config options; a tab file sets this to its ``OPTIONS``.
    options: ClassVar[Sequence[Option]] = ()
    #: Every tab scrolls, so a short window still reaches the bottom.
    scrolls: ClassVar[bool] = True
    #: Polled while hidden too (its poll does work, not only drawing).
    #: doc: docs/prs/gui-smooth.md#poll-only-the-shown-tab
    polls_hidden: ClassVar[bool] = False

    def __init__(self, ctx: GuiContext) -> None:
        self.ctx = ctx
        self._readers: dict[str, Callable[[], object]] = {}
        self._writers: dict[str, Callable[[object], None]] = {}
        self._switches: dict[str, ctk.CTkSwitch] = {}

    def build(self, parent: ctk.CTkFrame) -> None:
        """Create this tab's widgets inside *parent* (called once)."""
        raise NotImplementedError

    def on_poll(self, snap: WorldState) -> None:
        """Update widgets from a state snapshot (default: no-op)."""
        return None

    def build_options(self, parent: ctk.CTkFrame, options: list[Option] | None = None) -> None:
        """One labelled control per entry in *options* (default ``options``)."""
        if self.options and self.reload_options not in self.ctx.config_listeners:
            self.ctx.config_listeners.append(self.reload_options)
        cfg = load_real_config()
        for option in self.options if options is None else options:
            if option.label:
                row = Row(parent)
                row.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
                Label(
                    row,
                    text=option.label,
                    anchor="w",
                    font=ctk.CTkFont(size=theme.SIZE_BODY),
                    text_color=theme.TEXT_DIM,
                ).pack(side="left")
            get, put = _control(row, option, self.submit)
            put(read_option(cfg, option.field))
            self._readers[option.field] = get
            self._writers[option.field] = put
            if option.control == "switch":
                self._switches[option.field] = row.winfo_children()[-1]

    def reload_options(self) -> None:
        """Refill every control from the picked config."""
        cfg = load_real_config()
        for option in self.options:
            if option.field in self._writers:
                self._writers[option.field](read_option(cfg, option.field))

    def submit(self) -> None:
        """Save this tab and apply it live; a refused value goes to the log.

        Every control calls this when its value changes, so the picked
        config and the running bot always match the controls.
        """
        # doc: docs/prs/gui-calibration.md#live-apply
        try:
            self.apply()
        except ConfigError as exc:
            self.ctx.log.error(str(exc))
            return
        self.ctx.config_changed()

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


# Tk key names the device spells differently (firmware/moonlight/moonlight.ino).
_KEY_TOKENS = {"Return": "enter", "BackSpace": "backspace", "Prior": "pgup", "Next": "pgdn"}
_PLAIN_KEYS = {"tab", "space", "delete", "insert", "home", "end", "up", "down", "left", "right"}
# Windows Tk state bits.
_MODIFIERS = (("ctrl", 0x4), ("alt", 0x20000), ("shift", 0x1))


def combo_from_key(keysym: str, keycode: int, state: int) -> str | None:
    """The device combo for one key press, e.g. ``ctrl+F1``.

    None for a lone modifier or a key the device can't press.
    """
    if keysym[:1] == "F" and keysym[1:].isdigit() and 1 <= int(keysym[1:]) <= 24:
        key = keysym
    elif len(keysym) == 1 and keysym.isalpha():
        key = keysym.lower()
    elif 0x30 <= keycode <= 0x39:  # digit row; shift turns the keysym into "exclam" etc.
        key = chr(keycode)
    elif keysym in _KEY_TOKENS:
        key = _KEY_TOKENS[keysym]
    elif keysym.lower() in _PLAIN_KEYS:
        key = keysym.lower()
    else:
        return None
    return "+".join([name for name, bit in _MODIFIERS if state & bit] + [key])


class HotkeyBox(ctk.CTkFrame):  # type: ignore[misc]  # customtkinter is untyped
    """Click it, press a key: that key is the hotkey, saved at once.

    Escape cancels. The key press runs *on_submit* (the tab's save).
    """

    _listening: ClassVar[HotkeyBox | None] = None

    def __init__(self, parent: Any, on_submit: Callable[[], object] | None = None) -> None:
        super().__init__(parent, fg_color="transparent")
        self._on_submit = on_submit
        self._combo = ""
        self._bind_id: str | None = None
        self.btn = ctk.CTkButton(
            self,
            width=96,
            command=self._listen,
            fg_color=theme.NIGHT,
            hover_color=theme.HOVER,
            border_width=1,
            border_color=theme.RAISED,
            text_color=theme.TEXT,
            font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_BODY),
        )
        self.btn.pack(side="right")
        self.set("")

    def get(self) -> str:
        return self._combo

    def set(self, combo: str) -> None:
        self._stop()
        self._combo = combo
        self.btn.configure(text=combo or "set key", border_color=theme.RAISED)

    def _listen(self) -> None:
        if HotkeyBox._listening is not None:
            HotkeyBox._listening.set(HotkeyBox._listening.get())
        HotkeyBox._listening = self
        self.btn.configure(text="press a key", border_color=theme.MANA)
        self.focus_set()  # so no text box also gets the key
        self._bind_id = self.winfo_toplevel().bind("<KeyPress>", self._on_key, add="+")

    def _on_key(self, event: Any) -> str:
        if event.keysym == "Escape":
            self.set(self._combo)
            return "break"
        combo = combo_from_key(event.keysym, event.keycode, event.state)
        if combo is not None:
            self.set(combo)
            if self._on_submit is not None:
                self._on_submit()
        return "break"

    def _stop(self) -> None:
        if self._bind_id is not None:
            self.winfo_toplevel().unbind("<KeyPress>", self._bind_id)
            self._bind_id = None
        if HotkeyBox._listening is self:
            HotkeyBox._listening = None


def _control(
    row: ctk.CTkFrame, option: Option, on_submit: Callable[[], object] | None = None
) -> tuple[Callable[[], object], Callable[[object], None]]:
    """Pack *option*'s control into *row*; return functions to read and set it."""
    if option.control == "hotkey":
        hotkey = HotkeyBox(row, on_submit)
        hotkey.pack(side="right")
        return hotkey.get, lambda v: hotkey.set("" if v is None else str(v))
    if option.control == "switch":
        switch = ctk.CTkSwitch(row, text="", progress_color=theme.LIFE, command=on_submit)
        switch.pack(side="right")
        return lambda: bool(switch.get()), lambda v: switch.select() if v else switch.deselect()
    if option.control == "checkbox":
        box = ctk.CTkCheckBox(
            row, text="", width=24, fg_color=theme.LIFE, hover_color=theme.HOVER, command=on_submit
        )
        # Packed before the row's label: the far-right slot, even on a shared row.
        box.pack(side="right", padx=(theme.UNIT, 0), before=row.pack_slaves()[0])
        return lambda: bool(box.get()), lambda v: box.select() if v else box.deselect()
    if option.control == "dropdown":
        choices = option.choices or []
        menu = ctk.CTkOptionMenu(
            row,
            values=choices,
            fg_color=theme.RAISED,
            button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
            command=(lambda _: on_submit()) if on_submit else None,
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

        if on_submit is not None:
            slider.bind("<ButtonRelease-1>", lambda _e: on_submit())
            for event in ("<Return>", "<FocusOut>"):
                box.bind(event, lambda _e: on_submit())
        return get_slider, put_slider
    if option.off_at_zero:  # short number box right after the label
        entry = ctk.CTkEntry(row, width=60, fg_color=theme.NIGHT, border_color=theme.RAISED)
        entry.pack(side="left", padx=(theme.UNIT, 0))
    else:
        entry = ctk.CTkEntry(row, fg_color=theme.NIGHT, border_color=theme.RAISED)
        entry.pack(side="right")
    off = Label(row, text="off", text_color=theme.TEXT_MUTED)

    def shade(_e: object = None) -> None:
        # doc: docs/prs/potion-count.md#off-at-zero
        if not option.off_at_zero:
            return
        zero = entry.get().strip() in ("0", "")
        entry.configure(text_color=theme.TEXT_MUTED if zero else theme.TEXT)
        if zero:
            off.pack(side="left", after=entry, padx=(theme.UNIT, 0))
        else:
            off.pack_forget()

    entry.bind("<KeyRelease>", shade, add="+")
    if on_submit is not None:
        for event in ("<Return>", "<FocusOut>"):
            entry.bind(event, lambda _e: on_submit(), add="+")

    def put_entry(value: object) -> None:
        entry.delete(0, "end")
        entry.insert(0, "" if value is None else str(value))
        shade()

    return lambda: entry.get(), put_entry
