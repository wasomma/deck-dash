from __future__ import annotations

import time

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import AMBER, DIM, FG, GREEN, RED, TRACK, fmt_duration, new_key, text
from .base import Tile


def _since_color(seconds: float | None):
    if seconds is None:
        return GREEN
    if seconds < 86400:
        return RED
    if seconds < 7 * 86400:
        return AMBER
    return GREEN


def _crash_color(c: dict):
    return RED if c["kind"] == "bsod" else AMBER


def _short_since(seconds: float | None) -> str:
    if seconds is None:
        return "none"
    if seconds >= 86400:
        return f"{int(seconds // 86400)}d {int(seconds % 86400 // 3600)}h"
    return fmt_duration(seconds)


def _hex(c: dict | None) -> str:
    if not c:
        return "-"
    return f"0x{c['code']:X}" if c["code"] else "power"


class BsodTile(Tile):
    """Bugchecks are the headline (the nvlddmkm watch); power-loss reboots are counted separately."""

    name = "bsod"
    zoomable = True
    refresh = 5.0
    zoom_refresh = 5.0

    def render(self, now):
        src = self.sources["bsod"]
        s = src.state
        if not s:
            return self.placeholder("BSOD", src.error or "loading")
        img = new_key()
        d = ImageDraw.Draw(img)
        lb = s.get("last_bsod")
        since = (now - lb["time"]) if lb else None
        col = _since_color(since)
        text(d, (5, 8), "BSOD", 8, DIM, anchor="lm")
        n = s.get("bsod_in_window", 0)
        text(d, (67, 8), f"{n} in {int(s['window_days'])}d", 8, RED if n else GREEN, anchor="rm")
        text(d, (36, 27), _short_since(since), 20, col)
        text(d, (36, 41), "since last" if since is not None else "no bugchecks", 8, DIM, weight="semibold")
        if lb:
            text(d, (36, 52), f"last {_hex(lb)}", 9, RED, weight="semibold")
        text(d, (36, 62), f"up {fmt_duration(now - s['boot'])}", 8, DIM, weight="semibold")
        # 30-day strip along the bottom: one column per day, red for a bugcheck, amber for power loss.
        days = int(s["window_days"])
        w = 60 / days
        for i in range(days):
            x0 = 6 + i * w
            d.rectangle([x0, 67, x0 + w - 1, 69], fill=TRACK)
        for c in sorted(s.get("crashes", []), key=lambda c: c["kind"] == "bsod"):
            age_days = (now - c["time"]) / 86400
            if age_days < days:
                i = days - 1 - int(age_days)
                x0 = 6 + i * w
                d.rectangle([x0, 66, x0 + w - 1, 70], fill=_crash_color(c))
        return img

    def render_zoom(self, now):
        """Row 0: counters. Rows 1-2: the last ten unclean shutdowns, newest first, one per key."""
        src = self.sources["bsod"]
        s = src.state
        c = Canvas(self.gap)
        if not s:
            c.key_text(7, "no data", 16, DIM)
            c.key_text(12, (src.error or "")[:14], 9, DIM, weight="semibold")
            return c.slice()
        lb = s.get("last_bsod")
        since = (now - lb["time"]) if lb else None
        n_all = s.get("in_window", 0)
        n_bsod = s.get("bsod_in_window", 0)
        cells = [
            ("last bsod", _short_since(since), _since_color(since)),
            ("uptime", fmt_duration(now - s["boot"]), FG),
            ("bugchecks", str(n_bsod), RED if n_bsod else GREEN),
            ("power loss", str(n_all - n_bsod), AMBER if n_all - n_bsod else GREEN),
            ("last code", _hex(lb), FG),
        ]
        for i, (label, value, color) in enumerate(cells):
            c.key_text(i, label.upper(), 10, DIM, where="t", pad=8)
            c.key_text(i, value, 20, color, dy=8)
        c.key_text(2, f"{int(s['window_days'])} days", 9, DIM, where="b", pad=5, weight="semibold")
        c.key_text(3, f"{int(s['window_days'])} days", 9, DIM, where="b", pad=5, weight="semibold")
        if lb:
            c.key_text(4, lb["name"], 9, DIM, where="b", pad=5, weight="semibold")
        crashes = s.get("crashes", [])[:10]
        if not crashes:
            c.key_text(7, "clean log", 14, GREEN)
        for i, cr in enumerate(crashes):
            key = 5 + i
            lt = time.localtime(cr["time"])
            c.key_text(key, time.strftime("%b %d", lt).replace(" 0", " "), 12, FG, where="t", pad=6, weight="semibold")
            hh = lt.tm_hour % 12 or 12
            c.key_text(key, f"{hh}:{lt.tm_min:02d}{'p' if lt.tm_hour >= 12 else 'a'}", 10, DIM, where="c", dy=-2, weight="semibold")
            c.key_text(key, _hex(cr) if cr["kind"] == "bsod" else "power", 11, _crash_color(cr), where="b", pad=6, weight="semibold")
        return c.slice()
