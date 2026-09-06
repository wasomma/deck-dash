"""Claude Code hook: append the hook payload (from stdin) as one JSON line for the deck.

Wire it in ~/.claude/settings.json for SessionStart, UserPromptSubmit, Notification, Stop
and SessionEnd (see docs/claude-hooks.md). Standard library only; never blocks; exit 0.

    python tools/claude_hook.py                # reads the hook JSON from stdin
    python tools/claude_hook.py --event Notification --message "needs approval"   # for testing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "state" / "claude-events.jsonl"
KEEP = ("session_id", "cwd", "hook_event_name", "message", "title", "notification_type", "source", "reason")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--event", default=None)
    p.add_argument("--message", default="")
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--session", default="manual")
    args = p.parse_args()
    payload: dict = {}
    if args.event:
        payload = {"session_id": args.session, "cwd": args.cwd, "hook_event_name": args.event, "message": args.message}
    else:
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except (ValueError, OSError):
            payload = {}
    record = {k: payload.get(k) for k in KEEP if payload.get(k) is not None}
    record["event"] = record.pop("hook_event_name", None) or args.event or "?"
    record["t"] = time.time()
    try:
        EVENTS.parent.mkdir(exist_ok=True)
        with open(EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
