"""Health of the services on the VPS.

Two probes per service, both optional:
- ``ssh_host``: one SSH round trip runs ``systemctl is-active`` and a local curl for every
  service on the box, which is the only view that sees behind the auth portal;
- ``public``: an HTTPS GET from this PC for edge latency (2xx/3xx = reachable).
"""

from __future__ import annotations

import subprocess
import time
from collections import deque

import requests

from .base import Poller

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HIST = 30

_REMOTE_SCRIPT = (
    "for s in {specs}; do n=${{s%%|*}}; rest=${{s#*|}}; u=${{rest%%|*}}; l=${{rest#*|}}; "
    "a=$(systemctl is-active \"$u\" 2>/dev/null || echo unknown); "
    "if [ -n \"$l\" ]; then h=$(curl -s -m 3 -o /dev/null -w '%{{http_code}} %{{time_total}}' \"$l\" || echo '000 0'); "
    "else h='- 0'; fi; echo \"$n $a $h\"; done; echo \"_load $(cut -d' ' -f1-3 /proc/loadavg)\"; "
    "echo \"_up $(cut -d' ' -f1 /proc/uptime)\""
)


def parse_probe(text: str) -> dict:
    """Lines: ``name active|inactive|unknown http_code seconds``; ``_load a b c``; ``_up seconds``."""
    out: dict = {"services": {}, "load": None, "uptime": None}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "_load" and len(parts) >= 4:
            out["load"] = [float(x) for x in parts[1:4]]
        elif parts[0] == "_up" and len(parts) >= 2:
            out["uptime"] = float(parts[1])
        elif len(parts) >= 4:
            name, active, code, secs = parts[:4]
            try:
                ms = int(float(secs) * 1000)
            except ValueError:
                ms = None
            out["services"][name] = {"active": active, "local_code": code, "local_ms": ms}
    return out


def service_state(active: str | None, local_code: str | None, public_code: int | None) -> str:
    """ok / degraded / down / unknown from whatever probes answered."""
    if active is None and public_code is None:
        return "unknown"
    if active is not None:
        if active != "active":
            return "down"
        if local_code in (None, "-"):
            return "ok"
        try:
            n = int(local_code)
        except ValueError:
            n = 0
        return "ok" if 100 <= n < 500 else "degraded"
    return "ok" if public_code and 100 <= public_code < 500 else "down"


class VpsPoller(Poller):
    def __init__(self, cfg: dict):
        v = cfg.get("vps", {})
        super().__init__("vps", float(v.get("poll_seconds", 60)))
        self.ssh_host = str(v.get("ssh_host", "")).strip()
        self.services = [dict(s) for s in v.get("services", [])]
        self.hist: dict[str, deque] = {s["name"]: deque(maxlen=HIST) for s in self.services}

    def _ssh_probe(self) -> dict | None:
        if not self.ssh_host:
            return None
        specs = " ".join(f"'{s['name']}|{s.get('unit', s['name'])}|{s.get('local', '')}'" for s in self.services)
        script = _REMOTE_SCRIPT.format(specs=specs)
        t0 = time.perf_counter()
        p = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.ssh_host, script],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        )
        if p.returncode != 0:
            raise RuntimeError((p.stderr or "ssh failed").strip().splitlines()[-1][:120])
        probe = parse_probe(p.stdout)
        probe["ssh_ms"] = int((time.perf_counter() - t0) * 1000)
        return probe

    def _public(self, url: str) -> tuple[int | None, int | None]:
        t0 = time.perf_counter()
        try:
            r = requests.get(url, timeout=6, allow_redirects=False, headers={"User-Agent": "deck-dash/0.1"})
            return r.status_code, int((time.perf_counter() - t0) * 1000)
        except requests.RequestException:
            return None, None

    def fetch(self) -> dict:
        if not self.services:
            raise RuntimeError("not configured")
        ssh_error = None
        probe = None
        try:
            probe = self._ssh_probe()
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            ssh_error = str(exc)[:120]
        rows = []
        for s in self.services:
            name = s["name"]
            pub_code, pub_ms = self._public(s["public"]) if s.get("public") else (None, None)
            local = (probe or {}).get("services", {}).get(name) if probe else None
            state = service_state(local["active"] if local else None, local["local_code"] if local else None, pub_code)
            ms = pub_ms if pub_ms is not None else (local["local_ms"] if local else None)
            self.hist[name].append(ms if ms is not None else -1)
            rows.append({
                "name": name,
                "port": s.get("port"),
                "state": state,
                "active": local["active"] if local else None,
                "local_code": local["local_code"] if local else None,
                "local_ms": local["local_ms"] if local else None,
                "public_code": pub_code,
                "public_ms": pub_ms,
                "ms": ms,
                "hist": list(self.hist[name]),
            })
        return {
            "services": rows,
            "ssh_ok": probe is not None,
            "ssh_error": ssh_error,
            "ssh_ms": (probe or {}).get("ssh_ms"),
            "load": (probe or {}).get("load"),
            "uptime": (probe or {}).get("uptime"),
            "all_ok": all(r["state"] == "ok" for r in rows),
            "checked": time.time(),
        }
