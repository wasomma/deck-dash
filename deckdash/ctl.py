"""``deckdash ctl``: talk to the running deck-dash over its control pipe."""

from __future__ import annotations

import argparse
import json
import sys

from . import config
from .control import COMMANDS, DEFAULT_PIPE, restart_script, run_powershell, send

HELP = {
    "status": "mode, scene, brightness, slow ticks, every source's age and error",
    "wake": "back to the board (from a scene or a zoom)",
    "scene": "start an ambient scene now",
    "next": "the next scene in the rotation",
    "pause": "deck dark and the loop idle; the sources keep polling",
    "resume": "back on",
    "brightness": "a fixed level 0-100, or auto for the day/night schedule",
    "toast": "show a test toast",
    "reload": "re-read config.toml + config.local.toml: layout, scenes, brightness, idle",
    "quit": "exit (tools\\install_task.ps1 -Start brings it back)",
}

CLIENT_COMMANDS = {
    "restart": "stop and start the scheduled task (install_task.ps1 -Stop, then -Start)",
    "open": "open the dashboard window",
}


def build_parser() -> argparse.ArgumentParser:
    rows = [(COMMANDS[name][2], HELP.get(name, "")) for name in COMMANDS] + list(CLIENT_COMMANDS.items())
    epilog = "commands:\n" + "\n".join(f"  {usage:<28} {desc}" for usage, desc in rows)
    p = argparse.ArgumentParser(prog="deckdash ctl", description="Control the running deck-dash.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=epilog)
    p.add_argument("--json", action="store_true", help="print the raw reply")
    p.add_argument("--pipe", default=None, help="control pipe name (default: [ui] pipe in config.toml)")
    p.add_argument("command", choices=list(COMMANDS) + list(CLIENT_COMMANDS))
    p.add_argument("args", nargs="*")
    return p


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


def format_status(s: dict) -> str:
    override = s.get("brightness_override")
    head = (f"deck-dash {s.get('version', '?')} pid {s.get('pid', '?')}, up {_dur(s.get('uptime_s'))}, mode {s.get('mode')}"
            + (f" ({s['scene']})" if s.get("scene") else "")
            + f", brightness {s.get('brightness')} ({'auto' if override is None else f'fixed {override}'})")
    lines = [head,
             f"ticks {s.get('ticks', 0)}, slow ticks {s.get('slow_ticks', 0)}, last press {_dur(s.get('idle_s'))} ago, "
             f"badges: {', '.join(s.get('badges') or []) or 'none'}"]
    amb = s.get("ambient")
    if amb:
        lines.append(f"last ambient minute: {amb['scene']} {amb['fps']:.1f} fps (target {amb['target']:g}), "
                     f"flush avg {amb['flush_avg_ms']:.0f} ms max {amb['flush_max_ms']:.0f} ms, {amb['slow']} slow ticks")
    lines.append("sources:")
    for name, src in sorted((s.get("sources") or {}).items()):
        age = src.get("age_s")
        state = f"ok, {_dur(age)} ago" if age is not None else "no data yet"
        if src.get("error"):
            state += f"; failing x{src.get('failures', 0)}: {src['error']}"
        lines.append(f"  {name:<8} {state}")
    return "\n".join(lines)


def _configured_pipe() -> str:
    try:
        return str(config.load().get("ui", {}).get("pipe", DEFAULT_PIPE))
    except (OSError, ValueError):
        return DEFAULT_PIPE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pipe = args.pipe or _configured_pipe()
    if args.command == "restart":
        pid = run_powershell(restart_script(config.ROOT))
        print("restart requested" + (f" (powershell pid {pid})" if pid else "; not on Windows, nothing done"))
        return 0 if pid else 1
    if args.command == "open":
        print("the dashboard arrives in Phase 7b", file=sys.stderr)
        return 1
    reply = send(args.command, args.args, pipe=pipe)
    if args.json:
        print(json.dumps(reply, indent=2, sort_keys=True))
        return 0 if reply.get("ok") else 1
    if not reply.get("ok"):
        print(f"error: {reply.get('error', 'unknown')}", file=sys.stderr)
        return 1
    if args.command == "status":
        print(format_status(reply))
    else:
        extra = {k: v for k, v in reply.items() if k != "ok"}
        print("ok" + (" " + json.dumps(extra) if extra else ""))
    return 0
