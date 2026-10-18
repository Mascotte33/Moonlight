#!/usr/bin/env python3
"""Label golden screenshots for MOONLIGHT perception tests.

Commands:
    label     Interactively label a single screenshot.
    batch     Interactively label every image in a directory.
    validate  Check tests/golden/labels.json against the embedded JSON schema.
    schema    Print (or write to a file) the JSON schema for labels.json.

Only the Python standard library is used. Images are shown with the OS
default viewer when available; otherwise the user is told to open them
manually. Labels are keyed by image file name and merged into
tests/golden/labels.json (incrementally saved in batch mode).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = REPO_ROOT / "tests" / "golden" / "labels.json"

SCHEMA_VERSION = 1

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif"}

# (field name, input kind, prompt, default) -- int_or_null: empty/none = null
FIELD_SPECS: list[tuple[str, str, str, Any]] = [
    ("hp_percent", "float", "HP percentage (0-100)", 100.0),
    ("mp_percent", "float", "MP percentage (0-100)", 100.0),
    ("monster_count", "int", "Number of monsters visible", 0),
    ("selected_target", "int_or_null", "Selected target index (0-based) or empty for none", None),
    ("players_on_screen_count", "int", "Number of other players on screen", 0),
    ("position_x", "int", "Map position X", None),
    ("position_y", "int", "Map position Y", None),
    ("position_z", "int", "Map position Z", None),
]
NOTES_PROMPT = "Optional notes (empty to skip)"

SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "MOONLIGHT golden labels",
    "description": (
        "Labels for golden screenshots used by MOONLIGHT perception tests. "
        "Top level: 'version' (integer, must be 1) and 'frames', an object "
        "mapping image file name to a frame label. Cross-field rule: when "
        "selected_target is not null it is a 0-based index into the monster "
        "list, so 0 <= selected_target < monster_count. "
        "Position is the game map coordinate (x, y, z)."
    ),
    "type": "object",
    "required": ["version", "frames"],
    "additionalProperties": False,
    "properties": {
        "version": {"type": "integer", "const": 1},
        "frames": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": [
                    "hp_percent",
                    "mp_percent",
                    "monster_count",
                    "selected_target",
                    "players_on_screen_count",
                    "position_x",
                    "position_y",
                    "position_z",
                ],
                "additionalProperties": False,
                "properties": {
                    "hp_percent": {"type": "number", "minimum": 0, "maximum": 100},
                    "mp_percent": {"type": "number", "minimum": 0, "maximum": 100},
                    "monster_count": {"type": "integer", "minimum": 0},
                    "selected_target": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "0-based index of the selected monster, or null if none.",
                    },
                    "players_on_screen_count": {"type": "integer", "minimum": 0},
                    "position_x": {"type": ["integer", "null"], "minimum": 1, "maximum": 10000},
                    "position_y": {"type": ["integer", "null"], "minimum": 1, "maximum": 10000},
                    "position_z": {"type": ["integer", "null"], "minimum": -30, "maximum": 30},
                    "notes": {"type": "string"},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Minimal JSON-schema validator (supports the keywords used in SCHEMA only)
# ---------------------------------------------------------------------------

def _type_ok(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "null":
        return value is None
    return False


def validate_value(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    types = schema.get("type")
    if types is not None:
        type_list = [types] if isinstance(types, str) else list(types)
        if not any(_type_ok(value, t) for t in type_list):
            errors.append(
                f"{path}: expected type {type_list}, got {type(value).__name__} ({value!r})"
            )
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is less than minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is greater than maximum {schema['maximum']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required property '{req}'")
        props: dict[str, Any] = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                validate_value(value[key], sub, f"{path}.{key}", errors)
        extra = schema.get("additionalProperties")
        if extra is not None:
            for key in value:
                if key in props:
                    continue
                if extra is True:
                    continue
                if extra is False:
                    errors.append(f"{path}: additional property '{key}' is not allowed")
                elif isinstance(extra, dict):
                    validate_value(value[key], extra, f"{path}.{key}", errors)


def check_frame_rules(frame: Any, path: str, errors: list[str]) -> None:
    """Cross-field rule: selected_target must index an existing monster."""
    if not isinstance(frame, dict):
        return
    st = frame.get("selected_target")
    mc = frame.get("monster_count")
    st_ok = isinstance(st, int) and not isinstance(st, bool)
    mc_ok = isinstance(mc, int) and not isinstance(mc, bool)
    if st_ok and mc_ok and st >= mc:
        errors.append(
            f"{path}: selected_target ({st}) must be less than monster_count ({mc}) or null"
        )


def validate_document(doc: Any) -> list[str]:
    errors: list[str] = []
    validate_value(doc, SCHEMA, "$", errors)
    if isinstance(doc, dict):
        frames = doc.get("frames")
        if isinstance(frames, dict):
            for key, frame in frames.items():
                check_frame_rules(frame, f"$.frames.{key}", errors)
    return errors


# ---------------------------------------------------------------------------
# Interactive prompting
# ---------------------------------------------------------------------------

def ask_value(prompt: str, kind: str, default: Any) -> Any:
    """Prompt until a valid value is entered; empty input uses the default."""
    hint = f" [default: {default}]" if default is not None else ""
    while True:
        try:
            raw = input(f"{prompt}{hint}: ").strip()
        except EOFError:
            if default is not None:
                print(f"no input (EOF); using default: {default}")
                return default
            if kind == "text":
                return ""
            print("error: value required but no input (EOF)", file=sys.stderr)
            raise SystemExit(1) from None
        if raw == "":
            if default is not None:
                return default
            if kind == "text":
                return ""
            if kind == "int_or_null":
                return None  # empty means "no selection"
            print("a value is required")
            continue
        try:
            if kind == "int":
                return int(raw)
            if kind == "float":
                return float(raw)
            if kind == "int_or_null":
                if raw.lower() in ("null", "none", "n", "-"):
                    return None
                return int(raw)
            return raw  # text
        except ValueError:
            print(f"invalid value: {raw!r}")


def show_image(path: Path, no_open: bool = False) -> None:
    resolved = str(path.resolve())
    if no_open:
        print(f"Image (open it manually to view): {resolved}")
        return
    error: Exception | None = None
    try:
        if hasattr(os, "startfile"):  # Windows
            os.startfile(resolved)  # type: ignore[attr-defined]
            print(f"Opened: {resolved}")
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", resolved])
            print(f"Opened: {resolved}")
            return
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", resolved])
            print(f"Opened: {resolved}")
            return
    except Exception as exc:
        error = exc
    detail = f" ({error})" if error else ""
    print(f"Could not open the image automatically{detail}.")
    print("Please open this image manually and keep it visible while labeling:")
    print(f"  {resolved}")


def collect_frame(key: str, existing: dict[str, Any] | None) -> dict[str, Any]:
    print(f"\n=== Label: {key} ===")
    frame: dict[str, Any] = {}
    for name, kind, prompt, default in FIELD_SPECS:
        current = existing.get(name) if existing else None
        frame[name] = ask_value(prompt, kind, current if current is not None else default)
    while frame["selected_target"] is not None and frame["selected_target"] >= frame["monster_count"]:
        print(
            f"selected_target must be < monster_count ({frame['monster_count']}) "
            "or empty for none."
        )
        frame["selected_target"] = ask_value(
            "Selected target index (0-based) or empty for none", "int_or_null", None
        )
    notes = ask_value(NOTES_PROMPT, "text", None)
    if notes:
        frame["notes"] = notes
    for name in ("hp_percent", "mp_percent"):
        v = frame[name]
        if isinstance(v, float) and v.is_integer():
            frame[name] = int(v)
    return frame


# ---------------------------------------------------------------------------
# labels.json I/O
# ---------------------------------------------------------------------------

def load_labels(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": SCHEMA_VERSION, "frames": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {path} is not valid JSON ({exc}). Fix or delete it and retry.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("frames"), dict):
        raise SystemExit(f"error: {path} must be an object with a 'frames' object.")
    data.setdefault("version", SCHEMA_VERSION)
    return data


def save_labels(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_label(args: argparse.Namespace) -> int:
    image = Path(args.image)
    if not image.exists():
        print(f"error: image not found: {image}", file=sys.stderr)
        return 2
    labels_path = Path(args.labels)
    data = load_labels(labels_path)
    key = image.name
    existing = data["frames"].get(key)
    show_image(image, no_open=args.no_open)
    frame = collect_frame(key, existing)
    data["frames"][key] = frame
    save_labels(labels_path, data)
    print(f"\nSaved label for '{key}' to {labels_path}")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 2
    images = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not images:
        print(f"No images found in {directory} "
              f"(expected extensions: {', '.join(sorted(IMAGE_EXTENSIONS))})")
        return 0
    labels_path = Path(args.labels)
    data = load_labels(labels_path)
    labeled = skipped = 0
    for image in images:
        key = image.name
        existing = data["frames"].get(key)
        if existing is not None and not args.overwrite:
            print(f"Skipping {key} (already labeled; use --overwrite to re-label)")
            skipped += 1
            continue
        show_image(image, no_open=args.no_open)
        frame = collect_frame(key, existing)
        data["frames"][key] = frame
        save_labels(labels_path, data)
        labeled += 1
        print(f"Saved label for '{key}' to {labels_path}")
    print(f"\nDone: {labeled} labeled, {skipped} skipped -> {labels_path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    labels_path = Path(args.labels)
    if not labels_path.exists():
        print(f"error: file not found: {labels_path}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(labels_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"INVALID: {labels_path} is not valid JSON ({exc})", file=sys.stderr)
        return 1
    errors = validate_document(doc)
    if errors:
        print(f"INVALID: {labels_path} failed schema validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    n = len(doc["frames"])
    print(f"OK: {labels_path} is valid ({n} labeled frame(s))")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    text = json.dumps(SCHEMA, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote schema to {out}")
    else:
        print(text, end="")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="label_golden.py",
        description="Label golden screenshots for MOONLIGHT perception tests.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_label = sub.add_parser("label", help="interactively label a single image")
    p_label.add_argument("image", help="path to the screenshot")
    p_label.add_argument("--labels", default=str(DEFAULT_LABELS),
                         help="labels.json path (default: %(default)s)")
    p_label.add_argument("--no-open", action="store_true",
                         help="do not try to open the image automatically")
    p_label.set_defaults(func=cmd_label)

    p_batch = sub.add_parser("batch", help="interactively label all images in a directory")
    p_batch.add_argument("dir", help="directory with screenshots")
    p_batch.add_argument("--labels", default=str(DEFAULT_LABELS),
                         help="labels.json path (default: %(default)s)")
    p_batch.add_argument("--no-open", action="store_true",
                         help="do not try to open images automatically")
    p_batch.add_argument("--overwrite", action="store_true",
                         help="re-label frames that already have labels")
    p_batch.set_defaults(func=cmd_batch)

    p_validate = sub.add_parser("validate", help="validate labels.json against the schema")
    p_validate.add_argument("--labels", default=str(DEFAULT_LABELS),
                            help="labels.json path (default: %(default)s)")
    p_validate.set_defaults(func=cmd_validate)

    p_schema = sub.add_parser("schema", help="print the JSON schema for labels.json")
    p_schema.add_argument("out", nargs="?", default=None,
                          help="optional path to write the schema to")
    p_schema.set_defaults(func=cmd_schema)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
