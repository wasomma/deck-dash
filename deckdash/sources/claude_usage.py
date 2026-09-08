"""Claude usage, from the statusLine snapshot in ``state/claude-usage.json``.

``tools/claude_status.py`` runs as Claude Code's statusLine command and writes the file; this
source reads it back. The context window is per session (the newest one wins on the tile); the
5-hour and weekly limits are account-wide.

The weekly Fable limit is deliberately missing. Claude Code tracks only five_hour, seven_day,
seven_day_overage_included and overage from its response headers, and the statusLine payload
narrows that to five_hour and seven_day. The per-model weekly lives only in the /api/oauth/usage
API response, which needs a live OAuth token deck-dash does not hold.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .base import Poller
from .claude import project_name

ROOT = Path(__file__).resolve().parent.parent.parent
USAGE = ROOT / "state" / "claude-usage.json"


def _pct(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return max(0.0, float(v))


def _session(sid: str, s: dict, now: float) -> dict:
    ctx = s.get("ctx") if isinstance(s.get("ctx"), dict) else {}
    return {
        "id": sid,
        "project": project_name(str(s.get("cwd") or "")),
        "model": str(s.get("model") or ""),
        "ctx_pct": _pct(ctx.get("used_pct")),
        "ctx_size": _pct(ctx.get("size")) or 0.0,
        "ctx_in": _pct(ctx.get("in")) or 0.0,
        "ctx_out": _pct(ctx.get("out")) or 0.0,
        "age": max(0.0, now - float(s.get("t") or 0.0)),
    }


def read_usage(path: Path | str, now: float) -> dict:
    """The snapshot as the tile wants it. A missing or unreadable file is 'not wired', not an error."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        return {"statusline": False, "sessions": [], "checked": now}

    raw = rec.get("sessions")
    sessions = [_session(k, v, now) for k, v in raw.items() if isinstance(v, dict)] if isinstance(raw, dict) else []
    sessions.sort(key=lambda s: s["age"])
    top = sessions[0] if sessions else {}
    five = rec.get("five_hour") if isinstance(rec.get("five_hour"), dict) else {}
    week = rec.get("seven_day") if isinstance(rec.get("seven_day"), dict) else {}
    return {
        "statusline": True,
        "ctx_pct": top.get("ctx_pct"),
        "ctx_size": top.get("ctx_size", 0.0),
        "ctx_in": top.get("ctx_in", 0.0),
        "ctx_out": top.get("ctx_out", 0.0),
        "five_pct": _pct(five.get("used_pct")),
        "five_reset": _pct(five.get("resets_at")) or 0.0,
        "week_pct": _pct(week.get("used_pct")),
        "week_reset": _pct(week.get("resets_at")) or 0.0,
        "model": top.get("model", ""),
        "project": top.get("project", ""),
        "age": max(0.0, now - float(rec.get("t") or 0.0)),
        "sessions": sessions,
        "checked": now,
    }


class ClaudeUsagePoller(Poller):
    def __init__(self, cfg: dict):
        super().__init__("claude_usage", float(cfg.get("claude_usage", {}).get("poll_seconds", 5)))
        self.path = USAGE

    def fetch(self) -> dict:
        return read_usage(self.path, time.time())
