"""Configuration: config.toml (tracked) merged with config.local.toml (machine-local, gitignored)."""

from __future__ import annotations

import copy
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


def write_local(update: dict, local: Path | str | None = None) -> None:
    """Merge ``{section: {key: value}}`` into config.local.toml (flat sections only)."""
    local = Path(local) if local else LOCAL_CONFIG
    existing: dict = {}
    if local.exists():
        with open(local, "rb") as f:
            existing = tomllib.load(f)
    merged = _merge(existing, update)
    lines = ["# Machine-local overrides written by deck-dash; not committed.", ""]
    for section, values in merged.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    local.write_text("\n".join(lines), encoding="utf-8")
