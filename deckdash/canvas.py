"""A virtual full-deck canvas: 5x3 keys plus the bezel gaps between them.

Drawing on the canvas and slicing it into keys makes motion cross the bezels correctly.
Rule for zoom views: graphics (lines, fills, bars, big shapes) may span bezels; text must
stay inside one key, which is what ``key_text`` guarantees. The same geometry composes the
simulator image.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .gfx import BG, FG, KEY, fit_size, text


class Canvas:
    def __init__(self, gap: int, cols: int = 5, rows: int = 3, bg=BG):
        self.gap = gap
        self.cols = cols
        self.rows = rows
        self.w = cols * KEY + (cols - 1) * gap
        self.h = rows * KEY + (rows - 1) * gap
        self.img = Image.new("RGB", (self.w, self.h), bg)
        self.draw = ImageDraw.Draw(self.img)

    def key_box(self, idx: int) -> tuple[int, int, int, int]:
        r, c = divmod(idx, self.cols)
        x0 = c * (KEY + self.gap)
        y0 = r * (KEY + self.gap)
        return (x0, y0, x0 + KEY, y0 + KEY)

    def key_center(self, idx: int) -> tuple[float, float]:
        x0, y0, x1, y1 = self.key_box(idx)
        return ((x0 + x1) / 2, (y0 + y1) / 2)

    def row_box(self, row: int) -> tuple[int, int, int, int]:
        """Full-width band covering one row of keys."""
        y0 = row * (KEY + self.gap)
        return (0, y0, self.w, y0 + KEY)

    def rows_box(self, first: int, last: int) -> tuple[int, int, int, int]:
        """Full-width band from row ``first`` through row ``last`` inclusive, gaps included."""
        y0 = first * (KEY + self.gap)
        y1 = last * (KEY + self.gap) + KEY
        return (0, y0, self.w, y1)

    def span_box(self, first_key: int, last_key: int) -> tuple[int, int, int, int]:
        """Bounding box from one key's top-left to another key's bottom-right, gaps included."""
        x0, y0, _, _ = self.key_box(first_key)
        _, _, x1, y1 = self.key_box(last_key)
        return (x0, y0, x1, y1)

    def key_text(self, idx: int, s: str, size: int, fill=FG, where: str = "c", pad: int = 6, weight: str = "bold", dy: float = 0) -> int:
        """Draw text confined to one key. ``where``: c, t, b, tl, tr, bl, br. Returns the size used."""
        x0, y0, x1, y1 = self.key_box(idx)
        size = fit_size(s, KEY - 2 * pad, size, weight=weight)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        spots = {
            "c": ("mm", cx, cy),
            "t": ("mm", cx, y0 + pad + size / 2),
            "b": ("mm", cx, y1 - pad - size / 2),
            "tl": ("lm", x0 + pad, y0 + pad + size / 2),
            "tr": ("rm", x1 - pad, y0 + pad + size / 2),
            "bl": ("lm", x0 + pad, y1 - pad - size / 2),
            "br": ("rm", x1 - pad, y1 - pad - size / 2),
        }
        anchor, x, y = spots[where]
        text(self.draw, (x, y + dy), s, size, fill, weight=weight, anchor=anchor)
        return size

    def slice(self) -> list[Image.Image]:
        return [self.img.crop(self.key_box(i)) for i in range(self.cols * self.rows)]

    @classmethod
    def compose(cls, keys: list[Image.Image], gap: int, scale: int = 1, bezel=(22, 22, 26), cols: int = 5, rows: int = 3) -> Image.Image:
        """Simulator view: keys on a bezel-coloured board, optionally upscaled."""
        c = cls(gap, cols, rows, bg=bezel)
        for i, key in enumerate(keys):
            box = c.key_box(i)
            if key is not None:
                c.img.paste(key, box[:2])
            c.draw.rounded_rectangle(box, radius=6, outline=(52, 52, 60), width=1)
        if scale != 1:
            return c.img.resize((c.w * scale, c.h * scale), Image.NEAREST)
        return c.img
