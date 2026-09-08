"""Plan limits from Claude's own usage endpoint: the 5-hour window, the weekly one, per-model weeklies.

The Desktop app never runs a statusLine command, so the limits have no local file to be read out
of (see ``claude_usage``). ``GET /api/oauth/usage`` is where Claude Code itself gets them, and it
is the only source that also carries the **per-model** weekly windows. They arrive in a
self-describing ``limits`` array - ``kind``, ``percent``, and for a scoped window the model whose
``display_name`` the server supplies - so the Fable bar is labelled by the plan, not guessed here.

Note the units: on the wire ``utilization`` is already a percentage (8.0, 72.0). In the response
*headers* the same word means a 0-1 fraction, which is why Claude Code multiplies those by 100.

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
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .base import Poller

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPES = ("user:inference", "user:profile", "user:sessions:claude_code",
          "user:mcp_servers", "user:file_upload")
REFRESH_MARGIN_S = 900.0   # refresh with a quarter hour to spare rather than on the 401
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
    """One window from a named top-level field.

    ``utilization`` here is already a percentage (the wire format returns 8.0 and 72.0, not 0.08
    and 0.72) - unlike the response *headers*, where it is a fraction and Claude Code multiplies
    by 100. Getting that backwards would have put 7200% on the key.
    """
    if not isinstance(d, dict):
        return None
    u = _num(d.get("utilization"))
    if u is None:
        return None
    return {"key": key, "label": label, "pct": u, "resets_at": _resets(d.get("resets_at")),
            "critical": False, "active": False}


KIND_LABEL = {"session": "5H", "weekly_all": "WK"}


def _from_limits(rows: list) -> list[dict]:
    """The ``limits[]`` array, which describes itself: kind, percent, and the model a scoped
    window belongs to. Preferred over the named fields because the per-model window arrives here
    with the server's own display_name and there is nothing to guess."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        pct = _num(r.get("percent"))
        if pct is None:
            continue
        kind = str(r.get("kind") or "")
        model = ((r.get("scope") or {}).get("model") or {}).get("display_name") if isinstance(r.get("scope"), dict) else None
        label = KIND_LABEL.get(kind) or (str(model).upper()[:5] if model else kind.upper()[:5] or "LIMIT")
        out.append({
            "key": f"model:{str(model).lower()}" if model else kind,
            "label": label,
            "pct": pct,
            "resets_at": _resets(r.get("resets_at")),
            "critical": str(r.get("severity") or "") == "critical",
            "active": bool(r.get("is_active")),
        })
    return out


def parse_usage(payload: dict) -> dict:
    """The endpoint's reply as a list of windows, or an explanation of why there are none.

    The wire format puts the windows at the top level and repeats them in a self-describing
    ``limits`` array; Claude Code's own normalized shape nests them under ``rate_limits``. Both
    are accepted, ``limits`` first, so a change on either side degrades rather than breaks.
    """
    if not isinstance(payload, dict):
        return {"buckets": [], "why": "unreadable reply"}
    if payload.get("rate_limits_available") is False:
        # Explicit for API-key, Bedrock and Vertex sessions, and for a token minted without
        # user:profile - which is what `claude setup-token` produces.
        return {"buckets": [], "why": "plan limits unavailable for this token"}

    if isinstance(payload.get("limits"), list):
        buckets = _from_limits(payload["limits"])
        if buckets:
            return {"buckets": buckets, "why": ""}

    limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else payload
    buckets = [b for b in (_entry(limits.get(k), k, label) for k, label in FIXED) if b]
    scoped = limits.get("model_scoped")
    if isinstance(scoped, list):
        for m in scoped:
            name = str((m or {}).get("display_name") or "").strip() if isinstance(m, dict) else ""
            b = _entry(m, f"model:{name.lower()}", name.upper()[:5] or "MODEL")
            if b:
                buckets.append(b)
    if not any(b["key"].startswith("model:") for b in buckets):
        for key, label in (("seven_day_opus", "OPUS"), ("seven_day_sonnet", "SONNET")):
            b = _entry(limits.get(key), f"model:{label.lower()}", label[:5])
            if b:
                buckets.append(b)
    return {"buckets": buckets, "why": "" if buckets else "no windows in the reply"}


STALE = "stale"   # not an error: the token is simply due for a refresh


def read_token(path: Path = CREDENTIALS, now: float = 0.0, margin: float = 0.0) -> tuple[str | None, str]:
    """The access token, or why it is unusable. Never writes; never logs the token.

    ``margin`` reports a token that is still valid but close enough to expiry to be worth
    refreshing, so the refresh happens on a quiet poll rather than as a 401 mid-glance.
    """
    try:
        oauth = json.loads(path.read_text(encoding="utf-8")).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return None, NEEDS_LOGIN
    token = oauth.get("accessToken")
    if not token:
        return None, NEEDS_LOGIN
    if "user:profile" not in (oauth.get("scopes") or []):
        return None, "token lacks user:profile"   # a refresh cannot add a scope
    expires = _num(oauth.get("expiresAt"))
    if expires is not None and expires / 1000.0 - margin <= now:
        return None, STALE
    return str(token), ""


def refresh_token(path: Path = CREDENTIALS, now: float = 0.0) -> tuple[str | None, str]:
    """Trade the refresh token for a new access token and write the pair back.

    Claude Code refreshes this file itself whenever the CLI is used, but Wes works in the Desktop
    app, which keeps its own credential elsewhere - so left alone the file simply expires after
    about eight hours and the limit bars go dark until someone runs ``claude auth login``.

    The refresh token rotates, so the reply **must** be persisted or the old one is dead and the
    CLI is logged out. The write preserves every other key in the file (the mcpOAuth block), goes
    through a temp file that is re-read before ``os.replace``, and re-reads the credentials
    immediately beforehand so a refresh the CLI just did is used rather than overwritten.
    """
    try:
        whole = json.loads(path.read_text(encoding="utf-8"))
        oauth = dict(whole.get("claudeAiOauth") or {})
    except (OSError, ValueError, AttributeError):
        return None, NEEDS_LOGIN
    token = oauth.get("refreshToken")
    if not token:
        return None, NEEDS_LOGIN

    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": token,
        "client_id": CLIENT_ID,
        "scope": " ".join(oauth.get("scopes") or SCOPES),
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "deck-dash"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            reply = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = "expired" if exc.code in (400, 401) else str(exc.code)
        return None, f"refresh rejected ({detail}): claude auth login"
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return None, f"refresh failed: {type(exc).__name__}"

    access = reply.get("access_token")
    if not access:
        return None, "refresh reply had no token"
    oauth["accessToken"] = access
    if reply.get("refresh_token"):
        oauth["refreshToken"] = reply["refresh_token"]
    if _num(reply.get("expires_in")) is not None:
        oauth["expiresAt"] = int((now + float(reply["expires_in"])) * 1000)
    if reply.get("scope"):
        oauth["scopes"] = str(reply["scope"]).split()
    whole["claudeAiOauth"] = oauth

    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(whole, indent=2), encoding="utf-8")
        json.loads(tmp.read_text(encoding="utf-8"))   # never replace the real file with junk
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        return None, f"could not save the refreshed token: {type(exc).__name__}"
    return str(access), ""


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

        token, why = read_token(self.path, now, REFRESH_MARGIN_S)
        if token is None and why == STALE:
            token, why = refresh_token(self.path, now)
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
                # Rejected despite a token that looked live: the clock skewed, or the CLI rotated
                # underneath us. One refresh and one retry, then leave it alone.
                token, why = refresh_token(self.path, now)
                if token is None:
                    self._hold_until = now + AUTH_BACKOFF_S
                    self._last = {"buckets": [], "why": why or "sign-in rejected: claude auth login", "checked": now}
                    return dict(self._last)
                try:
                    payload = self._get(token)
                except urllib.error.HTTPError:
                    self._hold_until = now + AUTH_BACKOFF_S
                    self._last = {"buckets": [], "why": "sign-in rejected: claude auth login", "checked": now}
                    return dict(self._last)
            else:
                raise
        out = parse_usage(payload)
        out["checked"] = now
        self._last = out
        return dict(out)
