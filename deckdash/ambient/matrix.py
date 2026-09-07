"""Matrix rain: columns of half-width katakana (MS Gothic) falling with fading trails."""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

from ..gfx import FONT_DIR
from .base import Scene

COL_W = 12
ROW_H = 14
GLYPH_SIZE = 12
LEVELS = [(0, 0, 0), (0, 60, 22), (0, 110, 40), (0, 170, 60), (60, 235, 110), (200, 255, 210)]
BLACK = (0, 0, 0)


def _glyph_set() -> tuple[str, str | None]:
    for fname in ("msgothic.ttc", "YuGothR.ttc", "meiryo.ttc"):
        path = os.path.join(FONT_DIR, fname)
        if os.path.exists(path):
            return "".join(chr(c) for c in range(0xFF71, 0xFF9E)) + "0123456789", path
    return "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ*+-<>=:", None


class MatrixScene(Scene):
    name = "matrix"
    fps = 14.0

    def __init__(self, cfg, sources, seed=None):
        super().__init__(cfg, sources, seed)
        self.cols = self.w // COL_W
        self.rows = self.h // ROW_H + 2
        glyphs, path = _glyph_set()
        try:
            font = ImageFont.truetype(path, GLYPH_SIZE) if path else ImageFont.truetype(os.path.join(FONT_DIR, "consola.ttf"), GLYPH_SIZE)
        except OSError:
            font = ImageFont.load_default(size=GLYPH_SIZE)
        self.glyphs = glyphs
        # Atlas: one small sprite per glyph and brightness level; pasting beats text drawing 800 times a frame.
        self.atlas: list[list[Image.Image]] = []
        for ch in glyphs:
            row = []
            for level in LEVELS:
                im = Image.new("RGB", (COL_W, ROW_H), BLACK)
                ImageDraw.Draw(im).text((COL_W / 2, ROW_H / 2), ch, font=font, fill=level, anchor="mm")
                row.append(im)
            self.atlas.append(row)
        self.cells = [[self.rng.randrange(len(glyphs)) for _ in range(self.rows)] for _ in range(self.cols)]
        self.drops = []
        for c in range(self.cols):
            self.drops.append(self._new_drop(start_anywhere=True))
        self.blank = Image.new("RGB", (COL_W, ROW_H), BLACK)
        self.last_t = 0.0
        self.canvas.img.paste(BLACK, (0, 0, self.w, self.h))

    def _new_drop(self, start_anywhere: bool = False) -> dict:
        return {
            "y": self.rng.uniform(-self.rows, self.rows) if start_anywhere else self.rng.uniform(-self.rows * 0.8, -2),
            "speed": self.rng.uniform(2.8, 8.8),  # rows per second (was 0.35-1.1 per frame at 8 fps)
            "len": self.rng.randint(5, 16),
        }

    def draw(self, t: float) -> None:
        img = self.canvas.img
        dt = min(0.5, max(0.0, t - self.last_t))
        self.last_t = t
        for c, drop in enumerate(self.drops):
            drop["y"] += drop["speed"] * dt
            head = int(drop["y"])
            if head - drop["len"] > self.rows:
                self.drops[c] = self._new_drop()
                continue
            column = self.cells[c]
            for r in range(self.rows):
                if self.rng.random() < 0.16 * dt:  # glyph churn: 0.02 per frame at the old 8 fps
                    column[r] = self.rng.randrange(len(self.glyphs))
                d = head - r
                if d < 0 or d > drop["len"]:
                    level = 0
                elif d == 0:
                    level = 5
                else:
                    level = max(1, 4 - int(4 * d / drop["len"]))
                y = r * ROW_H
                if y >= self.h:
                    break
                sprite = self.blank if level == 0 else self.atlas[column[r]][level]
                img.paste(sprite, (c * COL_W, y))
