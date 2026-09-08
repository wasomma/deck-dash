"""Data sources: background pollers that cache a state dict the render loop reads."""

from __future__ import annotations

from .base import Poller, StaticSource
from .bitaxe import BitaxePoller
from .bsod import BsodPoller
from .ci import GhPoller
from .claude import ClaudePoller
from .claude_usage import ClaudeUsagePoller
from .gpu import GpuPoller
from .media import MediaSource
from .news import NewsPoller
from .sysmon import PingPoller, SysPoller
from .vps import VpsPoller
from .weather import WeatherPoller

__all__ = ["Poller", "StaticSource", "make_sources"]

SOURCE_CLASSES = {
    "weather": WeatherPoller,
    "sys": SysPoller,
    "gpu": GpuPoller,
    "ping": PingPoller,
    "bitaxe": BitaxePoller,
    "ci": GhPoller,
    "vps": VpsPoller,
    "bsod": BsodPoller,
    "news": NewsPoller,
    "claude": ClaudePoller,
    "claude_usage": ClaudeUsagePoller,
    "media": MediaSource,
}


def make_sources(cfg: dict) -> dict:
    return {name: cls(cfg) for name, cls in SOURCE_CLASSES.items()}
