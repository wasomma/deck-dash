from __future__ import annotations

import math
import time

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import DIM, FG, RED, TRACK, hbar, new_key, text
from .base import Tile


class ClockTile(Tile):
    name = "clock"
    zoomable = True

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        fps = float(cfg.get("deck", {}).get("clock_fps", 4))
        self.refresh = 1.0 / fps
        self.zoom_refresh = 1.0 / fps
        self.twelve = bool(cfg.get("clock", {}).get("twelve_hour", True))

    def _hand(self, d, cx, cy, angle_deg, length, width, color):
        a = math.radians(angle_deg - 90)
        d.line([(cx, cy), (cx + math.cos(a) * length, cy + math.sin(a) * length)], fill=color, width=width)

    def _hm(self, lt):
        if self.twelve:
            return f"{lt.tm_hour % 12 or 12}:{lt.tm_min:02d}" + ("p" if lt.tm_hour >= 12 else "a")
        return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"

    def render(self, now):
        img = new_key()
        d = ImageDraw.Draw(img)
        lt = time.localtime(now)
        cx, cy, r = 36, 25, 23
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=TRACK, width=2)
        for h in range(12):
            a = math.radians(h * 30 - 90)
            major = h % 3 == 0
            inner = r - (5 if major else 3)
            d.line(
                [(cx + math.cos(a) * inner, cy + math.sin(a) * inner), (cx + math.cos(a) * (r - 1), cy + math.sin(a) * (r - 1))],
                fill=DIM if major else TRACK, width=2 if major else 1,
            )
        sec = now % 60
        minute = lt.tm_min + sec / 60
        hour = (lt.tm_hour % 12) + minute / 60
        self._hand(d, cx, cy, hour * 30, r * 0.55, 3, FG)
        self._hand(d, cx, cy, minute * 6, r * 0.82, 2, FG)
        self._hand(d, cx, cy, sec * 6, r * 0.9, 1, RED)
        d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=FG)
        text(d, (36, 56), self._hm(lt), 12)
        text(d, (36, 67), time.strftime("%a %b %d", lt).replace(" 0", " "), 9, DIM)
        return img

    def render_zoom(self, now):
        """Row 0: seconds sweep. Row 1: H H : M M, one glyph per key. Row 2: the date, one word per key."""
        lt = time.localtime(now)
        c = Canvas(self.gap)
        d = c.draw
        x0, y0, x1, y1 = c.row_box(0)
        hbar(d, (x0 + 8, y0 + 40, x1 - 8, y0 + 52), (now % 60) / 60, RED)
        c.key_text(2, f"{lt.tm_sec:02d}", 26, FG, where="t", pad=4)
        c.key_text(0, "sec", 11, DIM, where="tl")
        hour = lt.tm_hour % 12 or 12 if self.twelve else lt.tm_hour
        hh = f"{hour:2d}" if self.twelve else f"{hour:02d}"
        mm = f"{lt.tm_min:02d}"
        for key, ch in zip((5, 6, 8, 9), hh + mm):
            if ch != " ":
                c.key_text(key, ch, 66, FG)
        if int(now) % 2 == 0:
            c.key_text(7, ":", 66, FG, dy=-4)
        if self.twelve:
            c.key_text(7, "PM" if lt.tm_hour >= 12 else "AM", 13, DIM, where="b")
        words = [
            time.strftime("%A", lt),
            time.strftime("%B", lt),
            str(lt.tm_mday),
            f"wk {int(time.strftime('%V', lt))}",
            f"day {lt.tm_yday}",
        ]
        for i, w in enumerate(words):
            c.key_text(10 + i, w, 20, FG if i < 3 else DIM)
        return c.slice()
