"""CPU, memory, disk, network throughput (psutil) and WAN latency (ping)."""

from __future__ import annotations

import re
import subprocess
import time
from collections import deque

import psutil

from .base import Poller
from .pdh import CpuClock

HIST = 60
_PING_RE = re.compile(r"time[=<]\s*(\d+)\s*ms", re.IGNORECASE)


class SysPoller(Poller):
    def __init__(self, cfg: dict, clock: CpuClock | None = None):
        super().__init__("sys", 1.0)
        self.clock = CpuClock() if clock is None else clock  # opened lazily on the poller thread
        self.disk = cfg.get("net", {}).get("disk", "C:/")
        self.cpu_hist: deque = deque(maxlen=HIST)
        self.down_hist: deque = deque(maxlen=HIST)
        self.up_hist: deque = deque(maxlen=HIST)
        self._last = None
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

    def fetch(self) -> dict:
        cpu = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(self.disk)
        io = psutil.net_io_counters()
        now = time.time()
        down = up = 0.0
        if self._last is not None:
            dt = max(1e-3, now - self._last[0])
            down = max(0.0, (io.bytes_recv - self._last[1]) / dt)
            up = max(0.0, (io.bytes_sent - self._last[2]) / dt)
        self._last = (now, io.bytes_recv, io.bytes_sent)
        self.cpu_hist.append(cpu)
        self.down_hist.append(down)
        self.up_hist.append(up)
        return {
            "cpu": cpu,
            "per_core": per_core,
            "ghz": self.clock.read() or 0.0,  # 0 = no PDH counter (psutil only knows the nominal clock)
            "mem_used": vm.used,
            "mem_total": vm.total,
            "disk_used": du.used,
            "disk_total": du.total,
            "down": down,
            "up": up,
            "cpu_hist": list(self.cpu_hist),
            "down_hist": list(self.down_hist),
            "up_hist": list(self.up_hist),
            "boot": psutil.boot_time(),
            "procs": len(psutil.pids()),
        }


class PingPoller(Poller):
    def __init__(self, cfg: dict):
        n = cfg.get("net", {})
        super().__init__("ping", float(n.get("ping_seconds", 5)))
        self.host = n.get("ping_host", "1.1.1.1")
        self.hist: deque = deque(maxlen=24)

    def fetch(self) -> dict:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            out = subprocess.run(
                ["ping", "-n", "1", "-w", "1000", self.host],
                capture_output=True, text=True, timeout=3, creationflags=flags,
            ).stdout
        except (subprocess.TimeoutExpired, OSError):
            out = ""
        m = _PING_RE.search(out)
        ms = int(m.group(1)) if m else None
        self.hist.append(ms if ms is not None else -1)
        return {"ms": ms, "hist": list(self.hist), "host": self.host}
