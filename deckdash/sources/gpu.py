"""NVIDIA GPU telemetry through NVML (nvidia-ml-py)."""

from __future__ import annotations

from collections import deque

from .base import Poller

HIST = 60


class GpuPoller(Poller):
    def __init__(self, cfg: dict):
        super().__init__("gpu", 1.0)
        self.index = int(cfg.get("gpu", {}).get("index", 0))
        self._handle = None
        self._nvml = None
        self.name = "GPU"
        self.util_hist: deque = deque(maxlen=HIST)
        self.temp_hist: deque = deque(maxlen=HIST)
        self.power_hist: deque = deque(maxlen=HIST)

    def _init(self) -> None:
        import pynvml

        pynvml.nvmlInit()
        self._nvml = pynvml
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(self.index)
        name = pynvml.nvmlDeviceGetName(self._handle)
        self.name = name.decode() if isinstance(name, bytes) else str(name)

    def fetch(self) -> dict:
        if self._handle is None:
            self._init()
        nv, h = self._nvml, self._handle
        util = nv.nvmlDeviceGetUtilizationRates(h)
        temp = nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)
        mem = nv.nvmlDeviceGetMemoryInfo(h)
        try:
            power = nv.nvmlDeviceGetPowerUsage(h) / 1000.0
        except nv.NVMLError:
            power = 0.0
        try:
            fan = nv.nvmlDeviceGetFanSpeed(h)
        except nv.NVMLError:
            fan = 0
        try:
            clock = nv.nvmlDeviceGetClockInfo(h, nv.NVML_CLOCK_GRAPHICS)
        except nv.NVMLError:
            clock = 0
        self.util_hist.append(float(util.gpu))
        self.temp_hist.append(float(temp))
        self.power_hist.append(power)
        return {
            "name": self.name,
            "util": float(util.gpu),
            "mem_util": float(util.memory),
            "temp": float(temp),
            "mem_used": int(mem.used),
            "mem_total": int(mem.total),
            "power": power,
            "fan": float(fan),
            "clock": int(clock),
            "util_hist": list(self.util_hist),
            "temp_hist": list(self.temp_hist),
            "power_hist": list(self.power_hist),
        }
