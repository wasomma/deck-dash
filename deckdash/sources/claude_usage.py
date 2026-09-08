"""Claude usage: the context window from the newest transcript, the limits from the statusLine snapshot.

Two inputs, because neither covers the deck on its own:

* ``state/claude-usage.json`` - written by ``tools/claude_status.py`` running as Claude Code's
  statusLine command. It carries everything, but **only terminal sessions run it**: the Desktop
  Code tab fires hooks and never invokes the statusLine command (measured 2026-09-07 - a fresh
  Desktop session wrote SessionStart and UserPromptSubmit to the hook log and never touched this
  file). So on a Desktop-only machine it is never written.
* ``~/.claude/projects/<slug>/<session>.jsonl`` - the transcript every session writes, Desktop
  included. Its last assistant record carries the token counts the context window is made of.

The transcript has no limits in it and no window size either, so ``[claude_usage] context_window``
supplies the denominator; the zoom view shows the raw token count as well, which is true whatever
the denominator is. Fresher input wins, so a terminal session's statusLine snapshot overrides the
transcript while it is current.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .base import Poller
from .claude import project_name

ROOT = Path(__file__).resolve().parent.parent.parent
USAGE = ROOT / "state" / "claude-usage.json"
PROJECTS = Path.home() / ".claude" / "projects"
TAIL_BYTES = 256_000   # transcripts reach tens of MB; the last assistant record is near the end
SCAN_S = 30.0          # how often to re-list transcripts, rather than every poll
LIVE_S = 3600.0        # a transcript untouched for this long is a session Wes has walked away from
MAX_SESSIONS = 8


def _pct(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return max(0.0, float(v))


def _iso(ts: str) -> float:
    """A transcript timestamp (``2026-09-08T01:18:34.977Z``) as unix seconds; 0.0 if unparseable."""
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, AttributeError, TypeError):
        return 0.0


def live_transcripts(root: Path = PROJECTS, now: float = 0.0, live_s: float = LIVE_S) -> list[Path]:
    """Every session transcript still being written, newest first.

    Wes runs several sessions at once, so picking only the newest made the bar flip between them
    with nothing on the key to say which one it meant. All of them are read and the key shows the
    worst, because the question the bar answers is "is anything about to run out", not "what is
    the last thing that spoke".
    """
    found: list[tuple[float, Path]] = []
    try:
        for p in root.glob("*/*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if now - m < live_s:
                found.append((m, p))
    except OSError:
        return []
    found.sort(key=lambda mp: -mp[0])
    return [p for _, p in found[:MAX_SESSIONS]]


def read_transcript(path: Path, window: float) -> dict | None:
    """Context tokens from the last assistant record. Reads only the tail: these files are huge.

    Sidechain records are subagent turns with their own context, not the session's, so they are
    skipped - the tile is about the conversation Wes is in.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read()
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    if size > TAIL_BYTES:
        text = text.split("\n", 1)[-1] if "\n" in text else ""  # drop the partial first line
    last = None
    for line in text.splitlines():
        line = line.strip()
        if not line or '"assistant"' not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") != "assistant" or o.get("isSidechain"):
            continue
        msg = o.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            last = o
    if last is None:
        return None
    u = last["message"]["usage"]
    used = sum(float(u.get(k) or 0.0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return {
        "ctx_pct": (used / window * 100.0) if window else None,
        "ctx_size": window,
        "ctx_in": used,
        "ctx_out": float(u.get("output_tokens") or 0.0),
        "model": str(last["message"].get("model") or ""),
        "project": project_name(str(last.get("cwd") or "")),
        "at": _iso(str(last.get("timestamp") or "")),
        "from": "transcript",
    }


def read_snapshot(path: Path | str, now: float) -> dict | None:
    """The statusLine snapshot, or None when no terminal session has ever written one."""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    raw = rec.get("sessions")
    sessions = sorted(((k, v) for k, v in raw.items() if isinstance(v, dict)),
                      key=lambda kv: -float(kv[1].get("t") or 0.0)) if isinstance(raw, dict) else []
    top = dict(sessions[0][1]) if sessions else {}
    ctx = top.get("ctx") if isinstance(top.get("ctx"), dict) else {}
    five = rec.get("five_hour") if isinstance(rec.get("five_hour"), dict) else {}
    week = rec.get("seven_day") if isinstance(rec.get("seven_day"), dict) else {}
    return {
        "ctx_pct": _pct(ctx.get("used_pct")),
        "ctx_size": _pct(ctx.get("size")) or 0.0,
        "ctx_in": _pct(ctx.get("in")) or 0.0,
        "ctx_out": _pct(ctx.get("out")) or 0.0,
        "five_pct": _pct(five.get("used_pct")),
        "five_reset": _pct(five.get("resets_at")) or 0.0,
        "week_pct": _pct(week.get("used_pct")),
        "week_reset": _pct(week.get("resets_at")) or 0.0,
        "model": str(top.get("model") or ""),
        "project": project_name(str(top.get("cwd") or "")),
        "at": float(rec.get("t") or 0.0),
        "from": "statusline",
    }


def merge(snapshot: dict | None, transcripts: list[dict], now: float) -> dict:
    """The key reports the worst context of any live session; the limits are snapshot-only.

    A statusLine snapshot for a session that also has a transcript is the better reading of the
    two (Claude Code computed it), so it replaces that session rather than doubling it up.
    """
    sessions = [dict(t) for t in transcripts]
    if snapshot and snapshot.get("ctx_pct") is not None:
        sessions = [t for t in sessions if t.get("project") != snapshot.get("project")]
        sessions.append({k: v for k, v in snapshot.items() if k not in ("five_pct", "five_reset", "week_pct", "week_reset")})
    sessions = [s for s in sessions if s.get("ctx_pct") is not None]
    sessions.sort(key=lambda s: -float(s["ctx_pct"]))
    if not sessions and snapshot is None:
        return {"source": "none", "sessions": [], "checked": now}

    worst = sessions[0] if sessions else {}
    out = {k: v for k, v in worst.items() if k not in ("at", "from")}
    for key in ("five_pct", "five_reset", "week_pct", "week_reset"):  # limits are snapshot-only
        out[key] = (snapshot or {}).get(key)
    freshest = max((float(s.get("at") or 0.0) for s in sessions), default=float((snapshot or {}).get("at") or 0.0))
    out.update({
        "source": worst.get("from", "statusline"),
        "sessions": [{"project": s.get("project", ""), "model": s.get("model", ""),
                      "ctx_pct": s.get("ctx_pct"), "ctx_in": s.get("ctx_in", 0.0),
                      "age": max(0.0, now - float(s.get("at") or 0.0))} for s in sessions],
        "age": max(0.0, now - freshest),
        "limits_age": max(0.0, now - float(snapshot["at"])) if snapshot else None,
        "checked": now,
    })
    return out


class ClaudeUsagePoller(Poller):
    def __init__(self, cfg: dict):
        c = cfg.get("claude_usage", {})
        super().__init__("claude_usage", float(c.get("poll_seconds", 5)))
        self.window = float(c.get("context_window", 1_000_000))
        self.live_s = float(c.get("session_minutes", 60)) * 60
        self.path = USAGE
        self._paths: list[Path] = []
        self._scan_due = 0.0
        self._cache: dict[Path, tuple[tuple, dict | None]] = {}

    def _read(self, path: Path) -> dict | None:
        """Parse a transcript only when it has actually changed: between turns nothing moves, and
        re-parsing every live session's tail every 5 s to learn that would be waste for its own sake."""
        try:
            st = path.stat()
            stamp = (st.st_mtime, st.st_size)
        except OSError:
            return None
        hit = self._cache.get(path)
        if hit is None or hit[0] != stamp:
            hit = (stamp, read_transcript(path, self.window))
            self._cache[path] = hit
        return hit[1]

    def fetch(self) -> dict:
        now = time.time()
        if now >= self._scan_due:  # a directory walk every poll would be wasteful
            self._paths = live_transcripts(now=now, live_s=self.live_s)
            self._cache = {p: v for p, v in self._cache.items() if p in self._paths}
            self._scan_due = now + SCAN_S
        # Filter on the age of the last assistant record, not the file's mtime: Claude Code touches
        # the transcript of a session that is merely open, so mtime called 30-hour-old sessions live
        # and let one of them own the key ahead of the session Wes was actually typing in.
        reads = [r for r in (self._read(p) for p in self._paths) if r and now - float(r["at"] or 0.0) < self.live_s]
        return merge(read_snapshot(self.path, now), reads, now)
