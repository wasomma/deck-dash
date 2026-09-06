"""Bitaxe (AxeOS) miner telemetry: ``GET http://<host>/api/system/info``, no auth."""

from __future__ import annotations

from collections import deque

import requests

from .base import Poller

HIST = 60  # samples; at 10 s polling this is ten minutes


def fmt_hash(ghs: float) -> str:
    """Hashrate given in GH/s: 1074.6 -> '1.07T', 812.0 -> '812G'."""
    if ghs >= 1000:
        v = ghs / 1000
        return f"{v:.2f}T" if v < 10 else f"{v:.1f}T"
    return f"{ghs:.0f}G"


def fmt_diff(d: float) -> str:
    """Share difficulty the way AxeOS shows it: 57040659706 -> '57.0G'."""
    for suffix, div in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if d >= div:
            return f"{d / div:.1f}{suffix}"
    return f"{d:.0f}"


def parse_axeos(j: dict) -> dict:
    """Pick the fields the tiles use out of the AxeOS system-info JSON."""
    pool = j.get("stratumURL") or ""
    if j.get("isUsingFallbackStratum"):
        pool = j.get("fallbackStratumURL") or pool
    return {
        "hash": float(j.get("hashRate", 0.0)),
        "hash_1m": float(j.get("hashRate_1m", j.get("hashRate", 0.0))),
        "hash_10m": float(j.get("hashRate_10m", 0.0)),
        "hash_1h": float(j.get("hashRate_1h", 0.0)),
        "expected": float(j.get("expectedHashrate", 0.0)),
        "temp": float(j.get("temp", 0.0)),
        "vr_temp": float(j.get("vrTemp", 0.0)),
        "power": float(j.get("power", 0.0)),
        "voltage_mv": float(j.get("voltage", 0.0)),
        "freq": int(j.get("frequency", j.get("actualFrequency", 0)) or 0),
        "core_mv": int(j.get("coreVoltageActual", 0) or 0),
        "fan_pct": float(j.get("fanspeed", 0.0)),
        "fan_rpm": int(j.get("fanrpm", 0) or 0),
        "best": float(j.get("bestDiff", 0) or 0),
        "best_session": float(j.get("bestSessionDiff", 0) or 0),
        "accepted": int(j.get("sharesAccepted", 0) or 0),
        "rejected": int(j.get("sharesRejected", 0) or 0),
        "uptime": int(j.get("uptimeSeconds", 0) or 0),
        "pool": pool.replace("stratum+tcp://", ""),
        "fallback": bool(j.get("isUsingFallbackStratum", 0)),
        "asic": str(j.get("ASICModel", "")),
        "version": str(j.get("version", "")),
        "paused": bool(j.get("miningPaused", False)),
        "overheat": bool(j.get("overheat_mode", 0)),
        "wifi_rssi": int(j.get("wifiRSSI", 0) or 0),
        "error_pct": float(j.get("errorPercentage", 0.0)),
    }


class BitaxePoller(Poller):
    def __init__(self, cfg: dict):
        b = cfg.get("bitaxe", {})
        super().__init__("bitaxe", float(b.get("poll_seconds", 10)))
        self.host = str(b.get("host", "")).strip()
        self.hist: deque = deque(maxlen=HIST)
        self.temp_hist: deque = deque(maxlen=HIST)
        self.best_seen = 0.0  # Phase 4 raises a toast when this goes up

    def fetch(self) -> dict:
        if not self.host:
            raise RuntimeError("no host")
        r = requests.get(f"http://{self.host}/api/system/info", timeout=5)
        r.raise_for_status()
        st = parse_axeos(r.json())
        self.hist.append(st["hash_1m"])
        self.temp_hist.append(st["temp"])
        st["hist"] = list(self.hist)
        st["temp_hist"] = list(self.temp_hist)
        st["new_best"] = 0 < self.best_seen < st["best"]
        self.best_seen = max(self.best_seen, st["best"])
        st["host"] = self.host
        return st
