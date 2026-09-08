from __future__ import annotations

from .base import Tile
from .bitaxe import BitaxeTile
from .bsod import BsodTile
from .ci import CiTile
from .claude import ClaudeTile
from .clock import ClockTile
from .cpu import CpuTile
from .gpu import GpuTile
from .net import NetTile
from .news import NewsTile
from .nowplaying import NowPlayingTile
from .overlay import OverlayTile
from .usage import UsageTile
from .vps import VpsTile
from .weather import ForecastTile, WeatherNowTile

TILES: dict[str, type[Tile]] = {
    "clock": ClockTile,
    "weather": WeatherNowTile,
    "forecast": ForecastTile,
    "gpu": GpuTile,
    "cpu": CpuTile,
    "net": NetTile,
    "bitaxe": BitaxeTile,
    "ci": CiTile,
    "vps": VpsTile,
    "bsod": BsodTile,
    "news": NewsTile,
    "claude": ClaudeTile,
    "nowplaying": NowPlayingTile,
    "usage": UsageTile,
}

__all__ = ["Tile", "TILES", "OverlayTile", "make_tile"]


def make_tile(name: str, cfg: dict, sources: dict) -> Tile:
    cls = TILES.get(name)
    if cls is None:
        raise KeyError(f"unknown tile '{name}' (known: {', '.join(TILES)})")
    return cls(cfg, sources)
