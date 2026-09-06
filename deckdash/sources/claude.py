"""Claude Code sessions, from hook events appended to ``state/claude-events.jsonl``.

``tools/claude_hook.py`` writes one JSON line per hook call. This source tails the file and
keeps one record per session: busy (a prompt was submitted), waiting (a Notification fired:
permission prompt or idle prompt), idle (Stop: the turn finished), gone (SessionEnd).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .base import Poller

ROOT = Path(__file__).resolve().parent.parent.parent
EVENTS = ROOT / "state" / "claude-events.jsonl"
MAX_BYTES = 1_000_000
KEEP_LINES = 300


def project_name(cwd: str) -> str:
    cwd = (cwd or "").replace("\\", "/").rstrip("/")
    if "/.claude/worktrees/" in cwd:
        repo = cwd.split("/.claude/worktrees/")[0].rsplit("/", 1)[-1]
        return f"{repo}/wt"
    return cwd.rsplit("/", 1)[-1] or "?"


def apply_event(sessions: dict, ev: dict) -> None:
    sid = ev.get("session_id") or "?"
    kind = ev.get("event") or ev.get("hook_event_name") or ""
    t = float(ev.get("t") or 0.0)
    if kind == "SessionEnd":
        sessions.pop(sid, None)
        return
    s = sessions.setdefault(sid, {"id": sid, "project": project_name(ev.get("cwd", "")), "state": "idle", "since": t, "message": "", "seen": 0.0, "first": t})
    if ev.get("cwd"):
        s["project"] = project_name(ev["cwd"])
    if kind in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStop"):
        new = "busy"
    elif kind == "Notification":
        new = "waiting"
        s["message"] = str(ev.get("message") or ev.get("title") or "")[:120]
    elif kind in ("Stop", "SessionStart"):
        new = "idle"
        if kind == "Stop":
            s["message"] = "turn finished"
    else:
        return
    if new != s["state"] or kind == "Notification":
        s["state"] = new
        s["since"] = t
    s["last"] = t


def summarize(sessions: dict, now: float, stale_s: float = 6 * 3600) -> dict:
    live = [s for s in sessions.values() if now - float(s.get("last", s["since"])) < stale_s]
    live.sort(key=lambda s: (s["state"] != "waiting", -float(s["since"])))
    return {
        "sessions": live,
        "waiting": sum(1 for s in live if s["state"] == "waiting" and s["since"] > s["seen"]),
        "busy": sum(1 for s in live if s["state"] == "busy"),
        "hooks": True,
        "checked": now,
    }


class ClaudePoller(Poller):
    def __init__(self, cfg: dict):
        super().__init__("claude", float(cfg.get("claude", {}).get("poll_seconds", 2)))
        self.path = EVENTS
        self.sessions: dict = {}
        self._offset = 0
        self._inode = None
        self.hooks_seen = False

    def _rotate(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-KEEP_LINES:]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            self._offset = self.path.stat().st_size
        except OSError:
            pass

    def ack(self, now: float | None = None) -> None:
        """Mark every waiting session as seen (the tile was pressed)."""
        now = now or time.time()
        for s in self.sessions.values():
            s["seen"] = now

    def fetch(self) -> dict:
        now = time.time()
        if not self.path.exists():  # hooks not wired yet: an empty board, not an error
            return {"sessions": [], "waiting": 0, "busy": 0, "hooks": False, "checked": now}
        st = self.path.stat()
        if st.st_size < self._offset:
            self._offset = 0  # rotated or truncated
        if st.st_size > self._offset:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    apply_event(self.sessions, json.loads(line))
                except (ValueError, TypeError):
                    continue
            self.hooks_seen = True
        if st.st_size > MAX_BYTES:
            self._rotate()
        return summarize(self.sessions, now)
