from __future__ import annotations

import math

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import CYAN, DIM, FG, arc_gauge, fmt_duration, hbar, heat, new_key, sparkline, text, vbar
from .base import Tile


class CpuTile(Tile):
    name = "cpu"
    zoomable = True
    refresh = 1.0
    zoom_refresh = 1.0

    def render(self, now):
        src = self.sources["sys"]
        s = src.state
        if not s:
            return self.placeholder("CPU", src.error or "loading")
        img = new_key()
        d = ImageDraw.Draw(img)
        gb = 1 << 30
        arc_gauge(d, (36, 32), 27, 6, s["cpu"] / 100, CYAN)
        text(d, (36, 19), "CPU", 8, DIM)
        text(d, (36, 33), f"{s['cpu']:.0f}%", 17)
        if s.get("ghz"):
            text(d, (36, 47), f"{s['ghz']:.1f} GHz", 9, DIM, weight="semibold")
        else:  # no PDH counter: the RAM line as before
            text(d, (36, 47), f"{s['mem_used'] / gb:.0f}/{s['mem_total'] / gb:.0f}G", 9, DIM, weight="semibold")
        hbar(d, (12, 63, 60, 68), s["mem_used"] / max(1, s["mem_total"]), CYAN)
        return img

    def render_zoom(self, now):
        """Row 0: stat cells. Row 1: 60 s total load. Row 2: one bar per thread, grouped per key."""
        s = self.sources["sys"].state
        c = Canvas(self.gap)
        d = c.draw
        if not s:
            c.key_text(7, "no data", 16, DIM)
            return c.slice()
        gb = 1 << 30
        cells = [
            ("cpu", f"{s['cpu']:.0f}%", FG),
            ("ram", f"{s['mem_used'] / gb:.1f}/{s['mem_total'] / gb:.0f}G", CYAN),
            ("disk c:", f"{100 * s['disk_used'] / max(1, s['disk_total']):.0f}%", FG),
            ("procs", str(s.get("procs", "?")), FG),
            ("uptime", fmt_duration(now - s["boot"]), FG),
        ]
        for i, (label, value, color) in enumerate(cells):
            c.key_text(i, label.upper(), 10, DIM, where="t", pad=8)
            c.key_text(i, value, 22, color, dy=8)
        gx0, gy0, gx1, gy1 = c.rows_box(1, 1)
        d.rectangle((gx0, gy0, gx1, gy1), fill=(12, 14, 20))
        sparkline(d, (gx0 + 4, gy0 + 20, gx1 - 4, gy1 - 4), s["cpu_hist"], CYAN, vmax=100, fill=(16, 56, 60), width=2)
        c.key_text(5, "load, last 60 s", 11, DIM, where="tl", pad=5, weight="semibold")
        bx0, by0, bx1, by1 = c.rows_box(2, 2)
        d.rectangle((bx0, by0, bx1, by1), fill=(12, 14, 20))
        cores = s["per_core"] or []
        per_key = max(1, math.ceil(len(cores) / 5))
        for key in range(5):
            kx0, ky0, kx1, ky1 = c.key_box(10 + key)
            group = cores[key * per_key:(key + 1) * per_key]
            slot = (kx1 - kx0 - 8) / per_key
            for j, v in enumerate(group):
                x = kx0 + 4 + slot * j
                vbar(d, (int(x + 1), ky0 + 18, int(x + slot - 1), ky1 - 4), v / 100, heat(v / 100))
        c.key_text(10, f"{len(cores)} threads", 11, DIM, where="tl", pad=4, weight="semibold")
        return c.slice()
