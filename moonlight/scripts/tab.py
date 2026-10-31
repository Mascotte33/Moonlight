"""Scripts tab: the scripts list with ticks and pauses beside a code editor.

A ticked script runs on its own, its pause (ms, empty = 1000) after it
ended; a ``script`` waypoint runs one by name. Save checks the code: a
broken script is saved, shown red and can't be ticked or tested. A
script that fails while running is unticked and its error shown.

# doc: docs/prs/scripts-page.md#scripts-tab
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from moonlight.core.config import BotConfig, ConfigError
from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config, picked_config
from moonlight.gui.tab_base import Label, Row, Tab, row_button, row_entry, section
from moonlight.perception.capture import focus_window
from moonlight.scripts.library import (
    delete_script,
    new_script,
    rename_script,
    routes_using,
    script_names,
)
from moonlight.scripts.scripts import (
    DEFAULT_PAUSE_MS,
    DEFAULT_SCRIPTS_DIR,
    MIN_PAUSE_MS,
    TEST_WAIT,
    ScriptsConfig,
    script_error,
)

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.core.state import WorldState

__all__ = ["ScriptsTab", "with_scripts"]


def with_scripts(cfg: BotConfig, scripts: ScriptsConfig) -> BotConfig:
    """*cfg* with *scripts* as its scripts section, checked like a loaded file."""
    data = cfg.to_dict()
    data["modules"]["scripts"] = {"ticked": list(scripts.ticked), "pause_ms": scripts.pause_ms}
    return BotConfig.from_dict(data)


class ScriptsTab(Tab):
    """The scripts list (tick, name, pause) beside the selected script's code."""

    title = "Scripts"
    polls_hidden = True  # a script that failed is unticked even while hidden

    def build(self, parent: ctk.CTkFrame) -> None:
        self._dir: Path = DEFAULT_SCRIPTS_DIR
        self._selected: str | None = None
        self._dirty = False
        #: The Test now script the message waits on, until it started or was missed.
        self._test_shown: str | None = None
        #: Script name -> why it fails (Save's check, or an error while running).
        self._errors: dict[str, str] = {}

        root = Row(parent)
        root.pack(fill="both", expand=True, padx=10, pady=10)
        for col, weight in enumerate((2, 3)):
            root.grid_columnconfigure(col, weight=weight, uniform="cards")
        root.grid_rowconfigure(0, weight=1)

        box = section(root, "Scripts")
        box.grid(row=0, column=0, sticky="nsew", padx=(0, theme.GAP))
        Label(
            box,
            text="Ticked: runs by itself. ms: the pause after it ends (empty = 1000).",
            anchor="w",
            justify="left",
            wraplength=300,
            text_color=theme.TEXT_DIM,
        ).pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.rows = Row(box)
        self.rows.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        buttons = Row(box)
        buttons.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        for text, command in (
            ("New", self._new),
            ("Rename", self._rename),
            ("Delete", self._delete),
        ):
            row_button(buttons, text, command, width=70)

        code = section(root, "Code")
        code.grid(row=0, column=1, sticky="nsew")
        self.lbl_name = Label(code, text="", anchor="w", font=ctk.CTkFont(weight="bold"))
        self.lbl_name.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
        self.txt_code = ctk.CTkTextbox(
            code, height=420, wrap="none", font=ctk.CTkFont(family=theme.MONO), undo=True
        )
        self.txt_code.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.UNIT, 0))
        self.txt_code.bind("<KeyRelease>", lambda _e: self._set_dirty(True))
        self.lbl_error = Label(code, text="", anchor="w", justify="left", text_color=theme.BLOOD)
        self.lbl_error.pack(fill="x", padx=theme.PAD)
        actions = Row(code)
        actions.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        self.btn_save = row_button(actions, "Save", self._save, width=70)
        self.btn_test = row_button(actions, "Test now", self._test, width=90)
        self.lbl_msg = Label(actions, text="", anchor="w", text_color=theme.TEXT_DIM)
        self.lbl_msg.pack(side="left", padx=theme.PAD)

        for name in script_names(self._dir):
            if error := self._file_error(name):
                self._errors[name] = error
        self.ctx.broken_scripts = set(self._errors)
        self.ctx.config_listeners.append(self._draw_rows)
        self._draw_rows()
        names = script_names(self._dir)
        self._select(names[0] if names else None)

    # -- config -------------------------------------------------------------

    def _config(self) -> ScriptsConfig:
        return load_real_config().scripts

    def _write(self, scripts: ScriptsConfig) -> bool:
        """Save *scripts* into the picked config and apply it; False when refused."""
        path = picked_config()
        try:
            cfg = with_scripts(load_real_config(path), scripts)
        except ConfigError as exc:
            self.lbl_msg.configure(text=str(exc))
            return False
        cfg.save(path)
        self.ctx.config_changed()
        return True

    def _tick(self, name: str, box: ctk.CTkCheckBox) -> None:
        cfg = self._config()
        ticked = [n for n in cfg.ticked if n != name]
        if box.get():
            if name in self._errors:
                box.deselect()
                self.lbl_msg.configure(text=f"{name} is broken: fix it and Save first")
                return
            ticked = [n for n in script_names(self._dir) if n in {*ticked, name}]
        self._write(ScriptsConfig(tuple(ticked), cfg.pause_ms))

    def _pause(self, name: str, entry: ctk.CTkEntry) -> None:
        cfg = self._config()
        pause_ms = {n: ms for n, ms in cfg.pause_ms.items() if n != name}
        text = entry.get().strip()
        if text:
            if not text.isdigit() or int(text) < MIN_PAUSE_MS:
                self.lbl_msg.configure(text=f"pause is whole ms, {MIN_PAUSE_MS} or more")
                self._draw_rows()
                return
            pause_ms[name] = int(text)
        if pause_ms != cfg.pause_ms:
            self._write(ScriptsConfig(cfg.ticked, pause_ms))

    def _untick(self, names: set[str]) -> None:
        cfg = self._config()
        if names & set(cfg.ticked):
            self._write(ScriptsConfig(tuple(n for n in cfg.ticked if n not in names), cfg.pause_ms))

    # -- list ---------------------------------------------------------------

    def _draw_rows(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        cfg = self._config()
        for name in script_names(self._dir):
            row = Row(self.rows)
            row.pack(fill="x", pady=(0, theme.UNIT))
            box = ctk.CTkCheckBox(row, text="", width=24)
            if name in cfg.ticked:
                box.select()
            box.configure(command=partial(self._tick, name, box))
            box.pack(side="left")
            ctk.CTkButton(
                row,
                text=name,
                anchor="w",
                width=150,
                command=partial(self._pick, name),
                fg_color=theme.HOVER if name == self._selected else theme.RAISED,
                hover_color=theme.HOVER,
                text_color=theme.BLOOD if name in self._errors else theme.TEXT,
            ).pack(side="left", padx=(theme.UNIT, 0))
            entry = row_entry(row, "", 64, lambda: None)
            entry.configure(placeholder_text=str(DEFAULT_PAUSE_MS))
            if name in cfg.pause_ms:
                entry.insert(0, str(cfg.pause_ms[name]))
            for event in ("<Return>", "<FocusOut>"):
                entry.bind(event, lambda _e, n=name, e=entry: self._pause(n, e))
            Label(row, text="ms", text_color=theme.TEXT_DIM).pack(side="left", padx=theme.UNIT)

    def _pick(self, name: str) -> None:
        if name == self._selected or not self._discard_ok():
            return
        self._select(name)

    def _select(self, name: str | None) -> None:
        self._selected = name
        self.txt_code.delete("1.0", "end")
        if name is not None:
            self.txt_code.insert("1.0", (self._dir / f"{name}.py").read_text(encoding="utf-8"))
        self.lbl_name.configure(text=name or "No scripts yet: press New")
        self._set_dirty(False)
        self._show_error()
        self._draw_rows()

    def _show_error(self) -> None:
        error = self._errors.get(self._selected or "", "")
        self.lbl_error.configure(text=f"Broken: {error}" if error else "")

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.btn_save.configure(text="Save *" if dirty else "Save")

    def _discard_ok(self) -> bool:
        return not self._dirty or messagebox.askyesno("Unsaved changes", "Discard unsaved changes?")

    def _file_error(self, name: str) -> str | None:
        return script_error((self._dir / f"{name}.py").read_text(encoding="utf-8"))

    def _set_error(self, name: str, error: str | None) -> None:
        if error is None:
            self._errors.pop(name, None)
        else:
            self._errors[name] = error
        self.ctx.broken_scripts = set(self._errors)

    def _scripts_changed(self, old: str | None, new: str | None) -> None:
        for listener in self.ctx.script_listeners:
            listener(old, new)

    # -- buttons ------------------------------------------------------------

    def _ask_name(self, title: str) -> str | None:
        dialog = ctk.CTkInputDialog(title=title, text="Name (letters, digits and _):")
        name = dialog.get_input()
        return name.strip() if name else None

    def _new(self) -> None:
        if not self._discard_ok() or not (name := self._ask_name("New script")):
            return
        try:
            new_script(self._dir, name)
        except ValueError as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        self._scripts_changed(None, name)
        self._select(name)
        self.lbl_msg.configure(text=f"Made {name}")

    def _rename(self) -> None:
        old = self._selected
        if old is None or not self._discard_ok() or not (name := self._ask_name(f"Rename {old}")):
            return
        try:
            rename_script(self._dir, Path(load_real_config().routes_dir), old, name)
        except (ValueError, OSError) as exc:
            self.lbl_msg.configure(text=str(exc))
            return
        error = self._errors.get(old)
        self._set_error(old, None)
        if error is not None:
            self._set_error(name, error)
        cfg = self._config()
        pause_ms = {(name if n == old else n): ms for n, ms in cfg.pause_ms.items()}
        ticked = {name if n == old else n for n in cfg.ticked}
        in_order = tuple(n for n in script_names(self._dir) if n in ticked)
        self._write(ScriptsConfig(in_order, pause_ms))
        self._scripts_changed(old, name)
        self._select(name)
        self.lbl_msg.configure(text=f"Renamed {old} to {name}")

    def _delete(self) -> None:
        name = self._selected
        if name is None:
            return
        routes_dir = Path(load_real_config().routes_dir)
        used = routes_using(routes_dir, name)
        question = f"Delete {name}?" + (f" It is used on {used} waypoints." if used else "")
        if not messagebox.askyesno("Delete script", question):
            return
        delete_script(self._dir, routes_dir, name)
        self._set_error(name, None)
        cfg = self._config()
        pause_ms = {n: ms for n, ms in cfg.pause_ms.items() if n != name}
        self._write(ScriptsConfig(tuple(n for n in cfg.ticked if n != name), pause_ms))
        self._scripts_changed(name, None)
        names = script_names(self._dir)
        self._dirty = False
        self._select(names[0] if names else None)
        self.lbl_msg.configure(text=f"Deleted {name}")

    def _save(self) -> None:
        name = self._selected
        if name is None:
            return
        source = self.txt_code.get("1.0", "end-1c")
        (self._dir / f"{name}.py").write_text(source, encoding="utf-8")
        self._set_dirty(False)
        error = script_error(source)
        self._set_error(name, error)
        if error is not None:
            self._untick({name})
        self._show_error()
        self._draw_rows()
        self.lbl_msg.configure(text=f"Saved {name}" + (" (broken)" if error else ""))

    def _runner(self) -> Any:
        controller = self.ctx.controller
        return None if controller is None else controller.loop.all_modules.scripts

    # doc: docs/prs/scripts-page.md#test-now
    def _test(self) -> None:
        name, runner = self._selected, self._runner()
        if name is None:
            return
        if self._dirty:
            self._save()
        if name in self._errors:
            self.lbl_msg.configure(text=f"{name} is broken: fix it and Save first")
        elif runner is None or not self.ctx.bot_running():
            self.lbl_msg.configure(text="Test now works while the bot runs or is paused")
        else:
            # The keys go to the window in front: the runner starts the test
            # once the client is there and read (the GUI may cover its HP bar).
            runner.request_test(name)
            in_front = focus_window(load_real_config().window_title)
            self.lbl_msg.configure(
                text=f"Waiting for Tibia to test {name}..."
                if in_front
                else f"Click the Tibia window: {name} waits {TEST_WAIT:g} s for it"
            )
            self._test_shown = name

    def _show_test(self, runner: Any) -> None:
        """Test now: started, or why it did not run."""
        runner.drop_late_test()
        if runner.test_missed is not None:
            self.lbl_msg.configure(text=runner.test_missed)
            runner.test_missed = self._test_shown = None
        elif self._test_shown is not None and runner.test_request is None:
            self.lbl_msg.configure(text=f"Ran {self._test_shown}")
            self._test_shown = None

    def on_poll(self, snap: WorldState) -> None:
        runner = self._runner()
        if runner is not None:
            self._show_test(runner)
        failed = getattr(runner, "failed", None)
        if not failed:
            return
        taken = dict(failed)
        failed.clear()
        for name, error in taken.items():
            self._set_error(name, error)
        self._untick(set(taken))
        self._show_error()
        self._draw_rows()
