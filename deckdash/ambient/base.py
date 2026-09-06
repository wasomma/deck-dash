"""Ambient scenes: full-deck animations drawn on the virtual canvas and sliced into keys.

A scene is created fresh each time it comes on, so its state starts clean. ``frame(now)``
returns the 15 key images; ``fps`` is the rate the app asks for (a full 15-key repaint
costs 60 ms on the gen-1 deck, so 8 fps is the comfortable ceiling at a 10 Hz tick).
"""

from __future__ import annotations

import random

from PIL import Image

from ..canvas import Canvas


class Scene:
    name = "scene"
    fps = 8.0

    def __init__(self, cfg: dict, sources: dict, seed: int | None = None):
        self.cfg = cfg
        self.sources = sources
        self.gap = int(cfg.get("deck", {}).get("gap_px", 24))
        self.canvas = Canvas(self.gap)
        self.w, self.h = self.canvas.w, self.canvas.h
        self.rng = random.Random(seed)
        self.t0: float | None = None

    def frame(self, now: float) -> list[Image.Image]:
        if self.t0 is None:
            self.t0 = now
        self.draw(now - self.t0)
        return self.canvas.slice()

    def draw(self, t: float) -> None:
        """Paint ``self.canvas`` for scene time ``t`` (seconds since the scene started)."""
        raise NotImplementedError
