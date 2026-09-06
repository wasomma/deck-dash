"""Ambient scenes shown after the idle timeout; any key press returns to the board."""

from __future__ import annotations

from .aquarium import AquariumScene
from .base import Scene
from .life import LifeScene
from .matrix import MatrixScene
from .plasma import PlasmaScene
from .tokyo import TokyoScene
from .weather import WeatherScene

SCENES: dict[str, type[Scene]] = {
    "weather": WeatherScene,
    "plasma": PlasmaScene,
    "life": LifeScene,
    "matrix": MatrixScene,
    "aquarium": AquariumScene,
    "tokyo": TokyoScene,
}


def make_scene(name: str, cfg: dict, sources: dict, seed: int | None = None) -> Scene:
    cls = SCENES.get(name)
    if cls is None:
        raise KeyError(f"unknown scene '{name}' (known: {', '.join(SCENES)})")
    return cls(cfg, sources, seed)
