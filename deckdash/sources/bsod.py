"""Crash history from the System event log via ``wevtutil`` (fast, no extra packages).

Kernel-Power 41 marks every unclean shutdown (BugcheckCode 0 = power loss or hard reset);
WER-SystemErrorReporting 1001 carries the bugcheck string after a BSOD. The two are merged
into one crash record when they land within ten minutes of each other.
"""

from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import psutil

from .base import Poller

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
QUERY = (
    "*[System[(EventID=41 and Provider[@Name='Microsoft-Windows-Kernel-Power']) or "
    "(EventID=1001 and Provider[@Name='Microsoft-Windows-WER-SystemErrorReporting'])]]"
)

# Bugcheck names worth showing by name; anything else is printed as hex.
BUGCHECK_NAMES = {
    0x0A: "IRQL",
    0x1E: "KMODE",
    0x3B: "SYSSVC",
    0x50: "PAGE_FLT",
    0x7E: "SYSTHRD",
    0xBE: "WR_RO_MEM",
    0xD1: "DRV_IRQL",
    0x116: "VIDEO_TDR",
    0x124: "WHEA",
    0x133: "DPC_WD",
    0x139: "KRN_SEC",
    0x1A: "MEM_MGMT",
    0xEF: "CRIT_PROC",
}


def _parse_time(s: str) -> float:
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def parse_events(xml_text: str) -> list[dict]:
    """Newest first. Each: {time, code (int), source ('41'/'1001'), kind ('bsod'/'power')}."""
    if not xml_text.strip():
        return []
    root = ET.fromstring(f"<Events>{xml_text}</Events>")
    out = []
    for ev in root.findall("e:Event", _NS):
        sys_ = ev.find("e:System", _NS)
        eid = int(sys_.findtext("e:EventID", "0", _NS))
        tc = sys_.find("e:TimeCreated", _NS)
        when = _parse_time(tc.get("SystemTime")) if tc is not None else 0.0
        data = {d.get("Name"): (d.text or "") for d in ev.findall("e:EventData/e:Data", _NS)}
        code = 0
        if eid == 41:
            try:
                code = int(data.get("BugcheckCode", "0") or 0)
            except ValueError:
                code = 0
        else:
            p1 = data.get("param1", "")
            try:
                code = int(p1.split()[0], 16) if p1 else 0
            except ValueError:
                code = 0
        out.append({"time": when, "code": code, "source": str(eid), "kind": "bsod" if code else "power"})
    out.sort(key=lambda e: -e["time"])
    return out


def merge_crashes(events: list[dict], window_s: float = 600) -> list[dict]:
    """Collapse a 41 and its 1001 into one crash; the bugcheck code wins over a zero."""
    crashes: list[dict] = []
    for ev in events:
        for c in crashes:
            if abs(c["time"] - ev["time"]) <= window_s:
                if ev["code"] and not c["code"]:
                    c["code"] = ev["code"]
                    c["kind"] = "bsod"
                c["time"] = max(c["time"], ev["time"])
                break
        else:
            crashes.append(dict(ev))
    for c in crashes:
        c["name"] = code_name(c["code"])
    return crashes


def code_name(code: int) -> str:
    if not code:
        return "power"
    return BUGCHECK_NAMES.get(code, f"0x{code:X}")


def summarize(crashes: list[dict], now: float, boot: float, window_days: float) -> dict:
    """``last`` is the newest unclean shutdown of any kind; ``last_bsod`` the newest real bugcheck."""
    last = crashes[0] if crashes else None
    last_bsod = next((c for c in crashes if c["kind"] == "bsod"), None)
    cutoff = now - window_days * 86400
    return {
        "crashes": crashes[:15],
        "last": last,
        "last_bsod": last_bsod,
        "since_last": (now - last["time"]) if last else None,
        "since_bsod": (now - last_bsod["time"]) if last_bsod else None,
        "in_window": sum(1 for c in crashes if c["time"] >= cutoff),
        "bsod_in_window": sum(1 for c in crashes if c["time"] >= cutoff and c["kind"] == "bsod"),
        "window_days": window_days,
        "boot": boot,
        "uptime": now - boot,
        "checked": now,
    }


class BsodPoller(Poller):
    def __init__(self, cfg: dict):
        b = cfg.get("bsod", {})
        super().__init__("bsod", float(b.get("poll_seconds", 60)))
        self.window_days = float(b.get("window_days", 30))
        self.max_events = int(b.get("max_events", 60))

    def fetch(self) -> dict:
        p = subprocess.run(
            ["wevtutil", "qe", "System", "/q:" + QUERY, f"/c:{self.max_events}", "/rd:true", "/f:xml"],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        )
        if p.returncode != 0:
            raise RuntimeError((p.stderr or "wevtutil failed").strip()[:120])
        crashes = merge_crashes(parse_events(p.stdout))
        return summarize(crashes, time.time(), psutil.boot_time(), self.window_days)
