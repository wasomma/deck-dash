"""Configuration: config.toml (tracked) merged with config.local.toml (machine-local, gitignored)."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"
LOCAL_CONFIG = ROOT / "config.local.toml"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load(path: Path | str | None = None, local: Path | str | None = None) -> dict:
    path = Path(path) if path else DEFAULT_CONFIG
    local = Path(local) if local else LOCAL_CONFIG
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if local.exists():
        with open(local, "rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    return cfg


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def _emit_table(lines: list[str], name: str, values: dict) -> None:
    """Scalars first, then sub-tables and arrays of tables, so the file re-parses to the same dict."""
    lines.append(f"[{name}]")
    nested = []
    for key, value in values.items():
        if isinstance(value, dict) or (isinstance(value, list) and value and all(isinstance(v, dict) for v in value)):
            nested.append((key, value))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    for key, value in nested:
        if isinstance(value, dict):
            _emit_table(lines, f"{name}.{key}", value)
        else:
            for row in value:
                lines.append(f"[[{name}.{key}]]")
                for k, v in row.items():
                    lines.append(f"{k} = {_toml_value(v)}")
                lines.append("")


def write_local(update: dict, local: Path | str | None = None) -> None:
    """Merge ``{section: {...}}`` into config.local.toml, keeping whatever else is there."""
    local = Path(local) if local else LOCAL_CONFIG
    existing: dict = {}
    if local.exists():
        with open(local, "rb") as f:
            existing = tomllib.load(f)
    merged = _merge(existing, update)
    lines = ["# Machine-local overrides written by deck-dash; not committed.", ""]
    for section, values in merged.items():
        if isinstance(values, dict):
            _emit_table(lines, section, values)
    # Written through a temp file and read back before it replaces the real one. This file is read
    # at every start, so a half-written or unparsable copy would leave the deck dark at the next
    # logon; the dashboard's settings form is the first thing that puts typed text in here.
    # It holds a full copy of the personal values, so it is gitignored and removed on every path
    # out of here - os.replace raises PermissionError on Windows whenever anything holds the
    # destination open, which the tray's Edit config does.
    tmp = local.with_suffix(".tmp.toml")
    try:
        tmp.write_text("\n".join(lines), encoding="utf-8")
        try:
            with open(tmp, "rb") as f:
                tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"refusing to write unparsable {local.name}: {exc}") from exc
        os.replace(tmp, local)
    finally:
        tmp.unlink(missing_ok=True)  # a no-op after a successful replace
