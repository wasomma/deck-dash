"""Plasma: a sum of drifting sine fields at quarter resolution, palette-mapped, upscaled."""

from __future__ import annotations

import colorsys
import math

import numpy as np
from PIL import Image

from .base import Scene


def _palette(hue0: float, hue_span: float) -> np.ndarray:
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        f = i / 255
        h = (hue0 + hue_span * f) % 1.0
        v = 0.25 + 0.75 * (0.5 - 0.5 * math.cos(f * math.pi * 2)) ** 0.8
        r, g, b = colorsys.hsv_to_rgb(h, 0.85, v)
        lut[i] = (int(r * 255), int(g * 255), int(b * 255))
    return lut


class PlasmaScene(Scene):
    name = "plasma"
    fps = 8.0
    SCALE = 4

    def __init__(self, cfg, sources, seed=None):
        super().__init__(cfg, sources, seed)
        self.gw, self.gh = math.ceil(self.w / self.SCALE), math.ceil(self.h / self.SCALE)
        ys, xs = np.mgrid[0:self.gh, 0:self.gw].astype(np.float32)
        self.xs, self.ys = xs, ys
        cx, cy = self.gw / 2, self.gh / 2
        self.rad = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        self.lut = _palette(self.rng.random(), 0.45 + 0.4 * self.rng.random())
        self.k1 = 10 + self.rng.random() * 8
        self.k2 = 6 + self.rng.random() * 6

    def draw(self, t: float) -> None:
        x, y = self.xs, self.ys
        v = (
            np.sin(x / self.k1 + t * 0.9)
            + np.sin(y / self.k2 - t * 0.7)
            + np.sin((x + y) / (self.k1 + self.k2) + t * 0.5)
            + np.sin(self.rad / 7.0 - t * 1.3)
            + np.sin((x * math.cos(t * 0.21) + y * math.sin(t * 0.17)) / 9.0)
        )
        idx = ((v + 5.0) / 10.0 * 255).astype(np.uint8)
        rgb = self.lut[idx]
        img = Image.fromarray(rgb, "RGB").resize((self.gw * self.SCALE, self.gh * self.SCALE), Image.NEAREST)
        self.canvas.img.paste(img.crop((0, 0, self.w, self.h)), (0, 0))
