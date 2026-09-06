"""Conway's Life on 8 px cells; colour follows cell age; reseeds when the world goes still."""

from __future__ import annotations

import numpy as np
from PIL import Image

from .base import Scene

CELL = 8
NEWBORN = np.array([120, 240, 255], dtype=np.float32)
OLD = np.array([70, 40, 160], dtype=np.float32)
BG = np.array([8, 10, 18], dtype=np.float32)


class LifeScene(Scene):
    name = "life"
    fps = 4.0

    def __init__(self, cfg, sources, seed=None):
        super().__init__(cfg, sources, seed)
        self.cols, self.rows = self.w // CELL, self.h // CELL
        self.generation = 0
        self.history: list[int] = []
        self.reseed()

    def reseed(self) -> None:
        density = 0.18 + 0.12 * self.rng.random()
        rs = np.random.RandomState(self.rng.randrange(1 << 30))
        self.alive = (rs.random_sample((self.rows, self.cols)) < density)
        self.age = np.zeros((self.rows, self.cols), dtype=np.int32)
        self.history.clear()
        self.generation = 0

    def step(self) -> None:
        a = self.alive.astype(np.int8)
        n = sum(np.roll(np.roll(a, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
        born = (~self.alive) & (n == 3)
        survive = self.alive & ((n == 2) | (n == 3))
        self.alive = born | survive
        self.age = np.where(self.alive, self.age + 1, 0)
        self.generation += 1
        pop = int(self.alive.sum())
        h = hash(self.alive.tobytes())
        stale = h in self.history[-6:]  # still life or short oscillator
        self.history.append(h)
        if pop < self.rows * self.cols * 0.02 or stale or self.generation > 900:
            self.reseed()

    def draw(self, t: float) -> None:
        if self.t0 is not None and t > 0:
            self.step()
        f = np.clip(self.age / 40.0, 0, 1)[..., None].astype(np.float32)
        rgb = np.where(self.alive[..., None], NEWBORN * (1 - f) + OLD * f, BG).astype(np.uint8)
        img = Image.fromarray(rgb, "RGB").resize((self.cols * CELL, self.rows * CELL), Image.NEAREST)
        # One-pixel gutters between cells keep the pixel-art look.
        px = np.array(img)
        px[CELL - 1::CELL, :, :] = BG.astype(np.uint8)
        px[:, CELL - 1::CELL, :] = BG.astype(np.uint8)
        board = Image.new("RGB", (self.w, self.h), tuple(int(v) for v in BG))
        board.paste(Image.fromarray(px, "RGB"), ((self.w - self.cols * CELL) // 2, (self.h - self.rows * CELL) // 2))
        self.canvas.img.paste(board, (0, 0))
