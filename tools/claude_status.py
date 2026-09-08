"""Claude Code statusLine command: cache the usage numbers for the deck, print one status line.

Claude Code hands its statusLine command a JSON payload on stdin. It carries ``context_window``
(``used_percentage``, ``context_window_size``, ``total_input_tokens``, ``total_output_tokens``)
and, once the account has seen a rate-limited response, ``rate_limits`` with ``five_hour`` and
``seven_day``, each ``{used_percentage, resets_at}`` where ``resets_at`` is unix epoch seconds.

The limits are account-wide, so they live at the top of the record; the context window is
per session, so each session gets its own entry and the tile shows the newest. ``rate_limits``
is absent until a bucket is live, so every field is merged over the previous record rather than
replacing it: an early call must not blank the meters.

Wire it in ~/.claude/settings.json (see docs/claude-hooks.md). Standard library only; never
blocks; exit 0 whatever happens.

    python tools/claude_status.py                         # reads the payload from stdin
    python tools/claude_status.py --sample payload.json    # for testing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USAGE = ROOT / "state" / "claude-usage.json"
KEEP_SESSIONS = 10
STALE_S = 6 * 3600


def _num(v) -> float | None:
    """A real number, or None. Bools are not numbers here."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _bucket(d) -> dict | None:
    pct = _num(d.get("used_percentage")) if isinstance(d, dict) else None
    if pct is None:
        return None
    return {"used_pct": pct, "resets_at": _num(d.get("resets_at")) or 0.0}


def snapshot(payload: dict, previous: dict | None = None, now: float | None = None) -> dict:
    """Merge one statusLine payload into the cached record."""
    now = time.time() if now is None else now
    out = dict(previous or {})
    out["t"] = now
    sessions = dict(out.get("sessions") or {})

    for name in ("five_hour", "seven_day"):
        b = _bucket((payload.get("rate_limits") or {}).get(name) if isinstance(payload.get("rate_limits"), dict) else None)
        if b:
            out[name] = b

    sid = str(payload.get("session_id") or "")
    if sid:
        s = dict(sessions.get(sid) or {})
        s["t"] = now
        if payload.get("cwd"):
            s["cwd"] = str(payload["cwd"])
        model = payload.get("model")
        if isinstance(model, dict) and model.get("display_name"):
            s["model"] = str(model["display_name"])
        cw = payload.get("context_window")
        if isinstance(cw, dict) and _num(cw.get("used_percentage")) is not None:
            s["ctx"] = {
                "used_pct": _num(cw.get("used_percentage")),
                "size": _num(cw.get("context_window_size")) or 0.0,
                "in": _num(cw.get("total_input_tokens")) or 0.0,
                "out": _num(cw.get("total_output_tokens")) or 0.0,
            }
        sessions[sid] = s

    live = sorted(((k, v) for k, v in sessions.items() if now - float(v.get("t") or 0.0) < STALE_S),
                  key=lambda kv: -float(kv[1].get("t") or 0.0))
    out["sessions"] = dict(live[:KEEP_SESSIONS])
    return out


def line(rec: dict) -> str:
    """The compact terminal status line: ``ctx 41% - 5h 12% - wk 30%``."""
    parts = []
    newest = max((s for s in (rec.get("sessions") or {}).values()), key=lambda s: float(s.get("t") or 0.0), default={})
    pct = ((newest.get("ctx") or {}).get("used_pct"))
    if pct is not None:
        parts.append(f"ctx {pct:.0f}%")
    for label, key in (("5h", "five_hour"), ("wk", "seven_day")):
        b = rec.get(key) or {}
        if b.get("used_pct") is not None:
            parts.append(f"{label} {b['used_pct']:.0f}%")
    return "  ·  ".join(parts)


def read(path: Path = USAGE) -> dict:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def write(rec: dict, path: Path = USAGE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", default=None, help="read the payload from this file instead of stdin")
    args = p.parse_args()
    try:
        raw = Path(args.sample).read_text(encoding="utf-8") if args.sample else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    rec = snapshot(payload, read())
    try:
        write(rec)
    except OSError:
        pass
    out = line(rec)
    if out:
        try:
            sys.stdout.write(out + "\n")
        except UnicodeEncodeError:  # a console on a legacy code page: an ASCII line beats a crash
            sys.stdout.write(out.replace("\u00b7", "|").encode("ascii", "replace").decode() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
