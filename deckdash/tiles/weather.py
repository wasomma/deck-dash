from __future__ import annotations

from PIL import ImageDraw

from .. import wx_icons
from ..canvas import Canvas
from ..gfx import BLUE, DIM, FG, TRACK, WARM, clamp, fit_size, new_key, text, vbar
from .base import Tile

DAY_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}


def _hour_label(iso: str, twelve: bool) -> str:
    h = int(iso[11:13])
    if not twelve:
        return f"{h:02d}"
    return f"{h % 12 or 12}{'p' if h >= 12 else 'a'}"


def _clock_label(iso: str, twelve: bool) -> str:
    h, m = int(iso[11:13]), int(iso[14:16])
    if not twelve:
        return f"{h:02d}:{m:02d}"
    return f"{h % 12 or 12}:{m:02d}{'p' if h >= 12 else 'a'}"


def _weekday(date: str) -> str:
    import datetime as _dt

    return DAY_NAMES[_dt.date.fromisoformat(date).weekday()]


class WeatherNowTile(Tile):
    name = "weather"
    zoomable = True
    zoom_refresh = 0.25

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        self.refresh = 1.0 / float(cfg.get("deck", {}).get("anim_fps", 8))
        self.twelve = bool(cfg.get("clock", {}).get("twelve_hour", True))

    def render(self, now):
        src = self.sources["weather"]
        st = src.state
        cur = st.get("current")
        if not cur:
            return self.placeholder("weather", src.error or "loading")
        imperial = st.get("units", "imperial") == "imperial"
        img = new_key()
        d = ImageDraw.Draw(img)
        ic = wx_icons.icon(40, cur["code"], cur["is_day"], now)
        img.paste(ic, (0, 1), ic)
        temp = f"{round(cur['temp'])}°"
        text(d, (55, 21), temp, fit_size(temp, 32, 24))
        text(d, (36, 47), wx_icons.label(cur["code"]), 11)
        wind_unit = "mph" if imperial else "km/h"
        line = f"feels {round(cur['feels'])}° · {round(cur['wind'])}{wind_unit}"
        text(d, (36, 62), line, fit_size(line, 68, 9, weight="semibold"), DIM, weight="semibold")
        return img

    def render_zoom(self, now):
        """Five-day forecast, one day per column; every text element lives inside a key."""
        st = self.sources["weather"].state
        c = Canvas(self.gap)
        days = st.get("daily") or []
        if not days:
            c.key_text(7, "no data", 16, DIM)
            return c.slice()
        for col, day in enumerate(days[:5]):
            x0, y0, x1, y1 = c.key_box(col)
            cx = (x0 + x1) / 2
            c.key_text(col, "Today" if col == 0 else _weekday(day["date"]), 13, FG if col == 0 else DIM, where="t", pad=3)
            ic = wx_icons.icon(48, day["code"], True, now)
            c.img.paste(ic, (int(cx - 24), y0 + 20), ic)
            c.key_text(5 + col, f"{round(day['hi'])}°", 24, WARM, where="t", pad=10)
            c.key_text(5 + col, f"{round(day['lo'])}°", 17, DIM, where="b", pad=8)
            x0, y0, x1, y1 = c.key_box(10 + col)
            vbar(c.draw, (x0 + 6, y0 + 8, x0 + 14, y0 + 40), day["precip"] / 100, BLUE)
            text(c.draw, (x0 + 42, y0 + 18), f"{int(day['precip'])}%", 15, BLUE)
            text(c.draw, (x0 + 42, y0 + 34), "rain", 9, DIM, weight="semibold")
            sun = f"{_clock_label(day['sunrise'], self.twelve)}  {_clock_label(day['sunset'], self.twelve)}"
            c.key_text(10 + col, sun, 9, DIM, where="b", pad=5, weight="semibold")
        return c.slice()


class ForecastTile(Tile):
    """Next five hours as five mini columns: hour, glyph, temperature, rain probability bar."""

    name = "forecast"
    zoomable = True
    zoom_refresh = 0.25

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        self.refresh = 1.0 / float(cfg.get("deck", {}).get("anim_fps", 8))
        self.twelve = bool(cfg.get("clock", {}).get("twelve_hour", True))

    def render(self, now):
        src = self.sources["weather"]
        hours = (src.state.get("hourly") or [])[:5]
        if not hours:
            return self.placeholder("next 5h", src.error or "loading")
        img = new_key()
        d = ImageDraw.Draw(img)
        for i, h in enumerate(hours):
            cx = 8 + 14 * i
            text(d, (cx, 5), _hour_label(h["time"], self.twelve), 8, DIM, weight="semibold")
            ic = wx_icons.icon(14, h["code"], h["is_day"], now + i * 0.4)
            img.paste(ic, (cx - 7, 10), ic)
            text(d, (cx, 31), f"{round(h['temp'])}", 10)
            vbar(d, (cx - 4, 40, cx + 4, 66), h["precip"] / 100, BLUE)
        return img

    def render_zoom(self, now):
        """Ten hours, two per key. Row 0 labels/icons, row 1 temperature curve, row 2 rain bars."""
        st = self.sources["weather"].state
        hours = (st.get("hourly") or [])[:10]
        c = Canvas(self.gap)
        d = c.draw
        if not hours:
            c.key_text(7, "no data", 16, DIM)
            return c.slice()
        temps = [h["temp"] for h in hours]
        lo, hi = min(temps), max(temps)
        xs = []
        for i, h in enumerate(hours):
            x0, y0, x1, y1 = c.key_box(i // 2)
            cx = x0 + 18 + 36 * (i % 2)
            xs.append(cx)
            text(d, (cx, y0 + 9), _hour_label(h["time"], self.twelve), 12, DIM)
            ic = wx_icons.icon(28, h["code"], h["is_day"], now + i * 0.3)
            c.img.paste(ic, (int(cx - 14), y0 + 18), ic)
            text(d, (cx, y0 + 60), f"{round(h['temp'])}°", 13)
        gx0, gy0, gx1, gy1 = c.rows_box(1, 1)
        d.rectangle((gx0, gy0, gx1, gy1), fill=(12, 14, 20))
        top, bottom = gy0 + 14, gy1 - 8
        pts = [(x, bottom - (bottom - top) * clamp((t - (lo - 2)) / max(1e-6, (hi + 2) - (lo - 2)))) for x, t in zip(xs, temps)]
        d.polygon([(pts[0][0], bottom)] + pts + [(pts[-1][0], bottom)], fill=(70, 40, 30))
        d.line(pts, fill=WARM, width=2)
        c.key_text(5, f"{round(hi)}°", 11, WARM, where="tl", pad=4)
        c.key_text(5, f"{round(lo)}°", 11, DIM, where="bl", pad=4)
        bx0, by0, bx1, by1 = c.rows_box(2, 2)
        d.rectangle((bx0, by0, bx1, by1), fill=(12, 14, 20))
        for i, h in enumerate(hours):
            cx = xs[i]
            vbar(d, (int(cx - 10), by0 + 6, int(cx + 10), by1 - 18), h["precip"] / 100, BLUE, track=TRACK)
            text(d, (cx, by1 - 9), f"{int(h['precip'])}%", 10, BLUE if h["precip"] >= 30 else DIM, weight="semibold")
        return c.slice()
