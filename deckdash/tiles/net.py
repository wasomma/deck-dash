from __future__ import annotations

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import BLUE, DIM, GREEN, RED, fmt_rate, heat, new_key, sparkline, text
from .base import Tile


def _ping_color(ms):
    if ms is None:
        return RED
    return heat((ms - 10) / 90)


class NetTile(Tile):
    name = "net"
    zoomable = True
    refresh = 1.0
    zoom_refresh = 1.0

    def render(self, now):
        src = self.sources["sys"]
        s = src.state
        p = self.sources["ping"].state
        if not s:
            return self.placeholder("net", src.error or "loading")
        img = new_key()
        d = ImageDraw.Draw(img)
        text(d, (36, 7), f"↓ {fmt_rate(s['down'])}", 10, BLUE)
        sparkline(d, (4, 13, 68, 31), s["down_hist"], BLUE, fill=(20, 38, 66))
        text(d, (36, 38), f"↑ {fmt_rate(s['up'])}", 10, GREEN)
        sparkline(d, (4, 44, 68, 58), s["up_hist"], GREEN, fill=(18, 52, 32))
        ms = p.get("ms") if p else None
        text(d, (36, 65), f"{ms} ms" if ms is not None else "no ping", 10, _ping_color(ms))
        return img

    def render_zoom(self, now):
        """Rows 0-1: download. Row 2: upload (keys 10-12) and WAN ping (keys 13-14)."""
        s = self.sources["sys"].state
        p = self.sources["ping"].state
        c = Canvas(self.gap)
        d = c.draw
        if not s:
            c.key_text(7, "no data", 16, DIM)
            return c.slice()
        gx0, gy0, gx1, gy1 = c.rows_box(0, 1)
        d.rectangle((gx0, gy0, gx1, gy1), fill=(12, 14, 20))
        sparkline(d, (gx0 + 4, gy0 + 26, gx1 - 4, gy1 - 4), s["down_hist"], BLUE, fill=(20, 38, 66), width=2)
        peak = max(s["down_hist"] or [0])
        c.key_text(0, f"↓ {fmt_rate(s['down'])}/s", 13, BLUE, where="tl", pad=5, weight="semibold")
        c.key_text(1, f"peak {fmt_rate(peak)}/s", 12, DIM, where="tl", pad=5, weight="semibold")
        c.key_text(4, "last 60 s", 12, DIM, where="tr", pad=5, weight="semibold")
        ux0, uy0, ux1, uy1 = c.span_box(10, 12)
        d.rectangle((ux0, uy0, ux1, uy1), fill=(12, 14, 20))
        sparkline(d, (ux0 + 4, uy0 + 24, ux1 - 4, uy1 - 4), s["up_hist"], GREEN, fill=(18, 52, 32), width=2)
        c.key_text(10, f"↑ {fmt_rate(s['up'])}/s", 13, GREEN, where="tl", pad=5, weight="semibold")
        px0, py0, px1, py1 = c.span_box(13, 14)
        d.rectangle((px0, py0, px1, py1), fill=(12, 14, 20))
        hist = [h if h >= 0 else 0 for h in (p.get("hist") or [])]
        ms = p.get("ms") if p else None
        sparkline(d, (px0 + 4, py0 + 24, px1 - 4, py1 - 4), hist, _ping_color(ms), vmax=max(50, max(hist or [0])), width=2)
        c.key_text(13, f"ping {ms} ms" if ms is not None else "ping timeout", 13, _ping_color(ms), where="tl", pad=5, weight="semibold")
        c.key_text(14, p.get("host", ""), 11, DIM, where="tr", pad=5, weight="semibold")
        return c.slice()
