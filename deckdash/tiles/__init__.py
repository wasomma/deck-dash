from __future__ import annotations

from .base import Tile
from .clock import ClockTile
from .cpu import CpuTile
from .gpu import GpuTile
from .net import NetTile
from .weather import ForecastTile, WeatherNowTile

TILES: dict[str, type[Tile]] = {
    "clock": ClockTile,
    "weather": WeatherNowTile,
    "forecast": ForecastTile,
    "gpu": GpuTile,
    "cpu": CpuTile,
    "net": NetTile,
}


def make_tile(name: str, cfg: dict, sources: dict) -> Tile:
    cls = TILES.get(name)
    if cls is None:
        raise KeyError(f"unknown tile '{name}' (known: {', '.join(TILES)})")
    return cls(cfg, sources)
