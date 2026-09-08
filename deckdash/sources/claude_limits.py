"""Plan limits from Claude's own usage endpoint: the 5-hour window, the weekly one, per-model weeklies.

The Desktop app never runs a statusLine command, so the limits have no local file to be read out
of (see ``claude_usage``). ``GET /api/oauth/usage`` is where Claude Code itself gets them, and it
is the only source that also carries the **per-model** weekly windows - ``model_scoped`` entries
whose ``display_name`` the server supplies, so the Fable bar is labelled by the server rather than
guessed here.

Two things this client must be careful about:

* **It rate-limits hard.** A couple of probes earned a 429 with ``retry-after: 3264`` (~54 min).
  The numbers move slowly - a 5-hour window shifts at most 0.33 % a minute - so the default poll
  is five minutes, and a 429 is honoured to the second rather than retried.
* **It never writes the credential file.** The token is read from ``~/.claude/.credentials.json``
  on each call, so a refresh by the CLI is picked up; when it has expired the tile is told to say
  so instead of the request being made at all. Refreshing it is Wes's to do (``claude auth login``).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .base import Poller

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
URL = "https://api.anthropic.com/api/oauth/usage"
TIMEOUT_S = 15.0
AUTH_BACKOFF_S = 900.0   # an expired token will not fix itself; asking every 5 min is just noise
NEEDS_LOGIN = "not signed in: claude auth login"

# The two account-wide windows, in the order they belong on the tile. Per-model windows come from
# `model_scoped` and are appended after these, named by the server.
FIXED = (("five_hour", "5H"), ("seven_day", "WK"))


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _resets(v) -> float:
    """``resets_at`` is an epoch int in some fields and an ISO string in others. Take either."""
    n = _num(v)
    if n is not None:
        return n
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0.0


def _entry(d, key: str, label: str) -> dict | None:
    """One window. ``utilization`` is a 0-1 fraction; the tile wants a percentage."""
    if not isinstance(d, dict):
        return None
    u = _num(d.get("utilization"))
    if u is None:
        return None
    return {"key": key, "label": label, "pct": u * 100.0, "resets_at": _resets(d.get("resets_at"))}


def parse_usage(payload: dict) -> dict:
    """The endpoint's reply as a list of windows, or an explanation of why there are none."""
    if not isinstance(payload, dict):
        return {"buckets": [], "why": "unreadable reply"}
    if payload.get("rate_limits_available") is False:
        # The endpoint says this explicitly for API-key, Bedrock and Vertex sessions, and for a
        # token minted without user:profile - which is what `claude setup-token` produces.
        return {"buckets": [], "why": "plan limits unavailable for this token"}
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return {"buckets": [], "why": "no rate_limits in the reply"}

    buckets = [b for b in (_entry(limits.get(k), k, label) for k, label in FIXED) if b]

    # Per-model weeklies: prefer `model_scoped`, whose display_name the server supplies, so the
    # label is whatever the plan actually calls that bucket rather than a guess made here.
    scoped = limits.get("model_scoped")
    if isinstance(scoped, list):
        for m in scoped:
            name = str((m or {}).get("display_name") or "").strip() if isinstance(m, dict) else ""
            b = _entry(m, f"model:{name.lower()}", name.upper()[:5] or "MODEL")
            if b:
                buckets.append(b)
    if not any(b["key"].startswith("model:") for b in buckets):
        # `model_scoped` is documented as additive and "present only when the server emits them",
        # so fall back to the named per-model fields rather than showing nothing.
        for key, label in (("seven_day_opus", "OPUS"), ("seven_day_sonnet", "SONNET")):
            b = _entry(limits.get(key), f"model:{label.lower()}", label[:5])
            if b:
                buckets.append(b)
    return {"buckets": buckets, "why": "" if buckets else "no windows in the reply"}


def read_token(path: Path = CREDENTIALS, now: float = 0.0) -> tuple[str | None, str]:
    """The access token and why it is unusable when it is. Never writes; never logs the token."""
    try:
        oauth = json.loads(path.read_text(encoding="utf-8")).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return None, NEEDS_LOGIN
    token = oauth.get("accessToken")
    if not token:
        return None, NEEDS_LOGIN
    expires = _num(oauth.get("expiresAt"))
    if expires is not None and expires / 1000.0 <= now:
        return None, "sign-in expired: claude auth login"
    if "user:profile" not in (oauth.get("scopes") or []):
        return None, "token lacks user:profile"
    return str(token), ""


class ClaudeLimitsPoller(Poller):
    def __init__(self, cfg: dict):
        c = cfg.get("claude_limits", {})
        super().__init__("claude_limits", float(c.get("poll_minutes", 5)) * 60)
        self.enabled = bool(c.get("enabled", True))
        self.path = CREDENTIALS
        self._hold_until = 0.0    # a 429's retry-after, or the backoff after an auth failure
        self._last: dict = {}

    def start(self) -> None:
        if not self.enabled:
            self.error = "disabled"   # the dashboard reads this as off, not broken
            return
        super().start()

    def _get(self, token: str) -> dict:
        req = urllib.request.Request(URL, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "deck-dash",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))

    def fetch(self) -> dict:
        now = time.time()
        if now < self._hold_until:  # honour a 429 to the second: this endpoint locks out for ~an hour
            return dict(self._last, held_for=self._hold_until - now, checked=now)

        token, why = read_token(self.path, now)
        if token is None:
            self._hold_until = now + AUTH_BACKOFF_S
            self._last = {"buckets": [], "why": why, "checked": now}
            return dict(self._last)
        try:
            payload = self._get(token)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = _num(exc.headers.get("retry-after")) or self.interval
                self._hold_until = now + wait
                self._last = dict(self._last, why=f"rate limited, {wait / 60:.0f} min", checked=now)
                return dict(self._last)
            if exc.code in (401, 403):
                self._hold_until = now + AUTH_BACKOFF_S
                self._last = {"buckets": [], "why": "sign-in rejected: claude auth login", "checked": now}
                return dict(self._last)
            raise
        out = parse_usage(payload)
        out["checked"] = now
        self._last = out
        return dict(out)
