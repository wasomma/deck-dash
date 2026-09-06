from __future__ import annotations

import math

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import DIM, FG, TRACK, fit_size, fmt_duration, hbar, heat, new_key, sparkline, text
from ..sources.bitaxe import fmt_diff, fmt_hash
from .base import Tile

BTC = (247, 147, 26)
BTC_DIM = (96, 60, 14)


def _pickaxe(d, cx: float, cy: float, t: float, size: float = 8.0, color=BTC, swing: bool = True) -> None:
    """A little pickaxe rocking about its grip; ``size`` is the handle half-length."""
    ang = math.radians(-35 + (math.sin(t * 5.0) * 28 if swing else 0))
    hx, hy = math.cos(ang), math.sin(ang)
    d.line([(cx - hx * size, cy - hy * size), (cx + hx * size, cy + hy * size)], fill=(150, 110, 60), width=2)
    tx, ty = cx + hx * size, cy + hy * size
    px, py = -hy, hx  # perpendicular to the handle
    w = size * 0.9
    d.line([(tx + px * w, ty + py * w - 1), (tx - px * w, ty - py * w - 1)], fill=color, width=3)


class BitaxeTile(Tile):
    name = "bitaxe"
    zoomable = True
    zoom_refresh = 1.0

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        self.refresh = 1.0 / float(cfg.get("deck", {}).get("anim_fps", 8))

    def render(self, now):
        src = self.sources["bitaxe"]
        b = src.state
        if not b:
            return self.placeholder("bitaxe", src.error or "loading")
        img = new_key()
        d = ImageDraw.Draw(img)
        hashing = not b["paused"] and b["hash_1m"] > 0
        text(d, (5, 8), "BITAXE", 8, DIM, anchor="lm")
        _pickaxe(d, 60, 10, now, swing=hashing, color=BTC if hashing else DIM)
        text(d, (36, 27), fmt_hash(b["hash_1m"]), 22, FG if hashing else DIM)
        tcol = heat((b["temp"] - 45) / 30)
        text(d, (36, 44), f"{b['temp']:.0f}°C · VR {b['vr_temp']:.0f}°", 9, tcol, weight="semibold")
        text(d, (36, 55), f"best {fmt_diff(b['best'])}", 8, DIM, weight="semibold")
        hbar(d, (12, 63, 60, 68), b["hash_1m"] / max(1.0, b["expected"]), BTC if hashing else TRACK)
        if b["overheat"]:
            text(d, (36, 36), "OVERHEAT", 11, (255, 84, 84))
        return img

    def render_zoom(self, now):
        """Row 0: live numbers. Row 1: 10 min hashrate. Row 2: shares, uptime, clocks, fan, pool."""
        src = self.sources["bitaxe"]
        b = src.state
        c = Canvas(self.gap)
        d = c.draw
        if not b:
            c.key_text(7, "no data", 16, DIM)
            c.key_text(12, (src.error or "")[:14], 9, DIM, weight="semibold")
            return c.slice()
        tcol = heat((b["temp"] - 45) / 30)
        vcol = heat((b["vr_temp"] - 50) / 35)
        cells = [
            ("hash 1m", fmt_hash(b["hash_1m"]), BTC),
            ("asic", f"{b['temp']:.0f}°C", tcol),
            ("vreg", f"{b['vr_temp']:.0f}°C", vcol),
            ("power", f"{b['power']:.1f}W", FG),
            ("best", fmt_diff(b["best"]), FG),
        ]
        for i, (label, value, color) in enumerate(cells):
            c.key_text(i, label.upper(), 10, DIM, where="t", pad=8)
            c.key_text(i, value, 22, color, dy=8)
        gx0, gy0, gx1, gy1 = c.rows_box(1, 1)
        d.rectangle((gx0, gy0, gx1, gy1), fill=(12, 14, 20))
        hist = b.get("hist") or []
        vmax = max(b["expected"] * 1.25, max(hist or [0]) * 1.05, 1.0)
        if b["expected"] > 0:
            y = gy1 - 4 - (gy1 - 4 - (gy0 + 20)) * (b["expected"] / vmax)
            d.line([(gx0 + 4, y), (gx1 - 4, y)], fill=BTC_DIM, width=1)
        sparkline(d, (gx0 + 4, gy0 + 20, gx1 - 4, gy1 - 4), hist, BTC, vmax=vmax, fill=(58, 38, 10), width=2)
        c.key_text(5, "hashrate, 10 min", 11, DIM, where="tl", pad=5, weight="semibold")
        c.key_text(9, f"exp {fmt_hash(b['expected'])}", 11, DIM, where="tr", pad=5, weight="semibold")
        c.key_text(8, f"1h {fmt_hash(b['hash_1h'])}", 11, DIM, where="tr", pad=5, weight="semibold")
        pool = b["pool"].split(":")[0]
        cells = [
            ("shares", f"{b['accepted']}/{b['rejected']}", FG),
            ("uptime", fmt_duration(b["uptime"]), FG),
            ("clock", f"{b['freq']}MHz", FG),
            ("fan", f"{b['fan_rpm']}rpm", FG),
            ("fallback" if b["fallback"] else "pool", pool, DIM),
        ]
        for i, (label, value, color) in enumerate(cells):
            c.key_text(10 + i, label.upper(), 10, DIM, where="t", pad=8)
            c.key_text(10 + i, value, fit_size(value, 62, 18), color, dy=8)
        return c.slice()
