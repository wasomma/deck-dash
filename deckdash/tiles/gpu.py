from __future__ import annotations

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import BLUE, DIM, FG, RED, arc_gauge, hbar, heat, new_key, sparkline, text
from .base import Tile


def _short_name(name: str) -> str:
    return name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")


class GpuTile(Tile):
    name = "gpu"
    zoomable = True
    refresh = 1.0
    zoom_refresh = 1.0

    def render(self, now):
        src = self.sources["gpu"]
        g = src.state
        if not g:
            return self.placeholder("GPU", src.error or "loading")
        img = new_key()
        d = ImageDraw.Draw(img)
        util, temp = g["util"], g["temp"]
        col = heat((temp - 35) / 50)
        arc_gauge(d, (36, 32), 27, 6, util / 100, col)
        text(d, (36, 19), "GPU", 8, DIM)
        text(d, (36, 33), f"{util:.0f}%", 17)
        text(d, (36, 47), f"{temp:.0f}°C", 10, col)
        hbar(d, (12, 63, 60, 68), g["mem_used"] / max(1, g["mem_total"]), BLUE)
        return img

    def render_zoom(self, now):
        """Row 0: five stat cells. Rows 1-2: 60 s utilisation (fill) and temperature (line)."""
        g = self.sources["gpu"].state
        c = Canvas(self.gap)
        d = c.draw
        if not g:
            c.key_text(7, "no data", 16, DIM)
            return c.slice()
        gb = 1 << 30
        temp_col = heat((g["temp"] - 35) / 50)
        cells = [
            ("util", f"{g['util']:.0f}%", FG),
            ("temp", f"{g['temp']:.0f}°C", temp_col),
            ("power", f"{g['power']:.0f} W", FG),
            ("fan", f"{g['fan']:.0f}%", FG),
            ("vram", f"{g['mem_used'] / gb:.1f}/{g['mem_total'] / gb:.0f}G", BLUE),
        ]
        for i, (label, value, color) in enumerate(cells):
            c.key_text(i, label.upper(), 10, DIM, where="t", pad=8)
            c.key_text(i, value, 22, color, dy=8)
        gx0, gy0, gx1, gy1 = c.rows_box(1, 2)
        d.rectangle((gx0, gy0, gx1, gy1), fill=(12, 14, 20))
        sparkline(d, (gx0 + 4, gy0 + 24, gx1 - 4, gy1 - 6), g["util_hist"], BLUE, vmax=100, fill=(24, 44, 78), width=2)
        sparkline(d, (gx0 + 4, gy0 + 24, gx1 - 4, gy1 - 6), g["temp_hist"], RED, vmax=100, width=1)
        c.key_text(5, _short_name(g["name"]), 12, DIM, where="tl", pad=5, weight="semibold")
        c.key_text(6, f"{g['clock']} MHz", 12, DIM, where="tl", pad=5, weight="semibold")
        c.key_text(7, "last 60 s", 12, DIM, where="t", pad=5, weight="semibold")
        c.key_text(8, "util", 12, BLUE, where="tr", pad=5, weight="semibold")
        c.key_text(9, "temp", 12, RED, where="tr", pad=5, weight="semibold")
        return c.slice()
