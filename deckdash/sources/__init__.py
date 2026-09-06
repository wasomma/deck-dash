"""Data sources: background pollers that cache a state dict the render loop reads."""

from __future__ import annotations

from .base import Poller, StaticSource
from .gpu import GpuPoller
from .sysmon import PingPoller, SysPoller
from .weather import WeatherPoller

__all__ = ["Poller", "StaticSource", "make_sources"]


def make_sources(cfg: dict) -> dict:
    return {
        "weather": WeatherPoller(cfg),
        "sys": SysPoller(cfg),
        "gpu": GpuPoller(cfg),
        "ping": PingPoller(cfg),
    }
