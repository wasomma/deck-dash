"""Weather as ambient: the whole deck becomes the sky outside, with the time and temperature on
top. Sky colour follows the real sunrise/sunset, clouds drift with the wind, rain, snow, fog and
lightning follow the current conditions.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PIL import Image, ImageDraw

from .. import wx_icons
from ..gfx import FG, fit_size
from .base import Scene

NIGHT = ((5, 7, 22), (12, 16, 44))
DUSK = ((44, 30, 84), (255, 128, 64))
DAY = ((38, 108, 222), (132, 190, 250))
GRAY = ((70, 76, 88), (120, 126, 138))
STORM = ((28, 30, 40), (54, 58, 70))
SUN = (255, 208, 70)
MOON = (226, 230, 240)
RAIN = (150, 190, 255)
SNOW = (245, 248, 255)


def _lerp(a, b, f):
    return tuple(int(x + (y - x) * f) for x, y in zip(a, b))


def _minute_of(iso: str, fallback: float) -> float:
    try:
        return int(iso[11:13]) * 60 + int(iso[14:16])
    except (ValueError, TypeError, IndexError):
        return fallback


class WeatherScene(Scene):
    name = "weather"
    fps = 6.0

    def __init__(self, cfg, sources, seed=None):
        super().__init__(cfg, sources, seed)
        st = sources["weather"].state if "weather" in sources else {}
        self.cur = st.get("current") or {}
        self.units = st.get("units", "imperial")
        self.twelve = bool(cfg.get("clock", {}).get("twelve_hour", True))
        daily = st.get("daily") or []
        today = daily[0] if daily else {}
        self.sunrise = _minute_of(today.get("sunrise", ""), 6 * 60 + 30)
        self.sunset = _minute_of(today.get("sunset", ""), 19 * 60 + 30)
        code = int(self.cur.get("code", 0))
        self.cat = wx_icons.category(code)
        self.wind = float(self.cur.get("wind", 6.0))
        r = self.rng
        self.stars = [(r.uniform(0, self.w), r.uniform(0, self.h * 0.8), r.uniform(0, 6.28), r.uniform(0.6, 1.6)) for _ in range(45)]
        n_clouds = {"clear": 1, "partly": 3, "overcast": 6, "fog": 2, "drizzle": 5, "rain": 6, "snow": 5, "thunder": 6}[self.cat]
        self.clouds = []
        for i in range(n_clouds):
            depth = r.uniform(0.5, 1.0)
            self.clouds.append({"x": r.uniform(-80, self.w), "y": r.uniform(6, self.h * 0.5), "w": r.uniform(70, 150) * depth, "depth": depth})
        n_drops = {"drizzle": 45, "rain": 110, "thunder": 80}.get(self.cat, 0)
        self.drops = [{"x": r.uniform(0, self.w), "y": r.uniform(0, self.h), "len": r.uniform(6, 14), "speed": r.uniform(220, 340)} for _ in range(n_drops)]
        self.flakes = [{"x": r.uniform(0, self.w), "y": r.uniform(0, self.h), "r": r.uniform(1.2, 3.0), "speed": r.uniform(18, 40), "phase": r.uniform(0, 6.28)} for _ in range(40 if self.cat == "snow" else 0)]
        self.flash = 0.0
        self.next_flash = r.uniform(3, 8)
        self.bolt = None
        self.last_t = 0.0
        self.overlay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))

    # --- sky -------------------------------------------------------------------------
    def _sky(self, minute: float):
        """Top/bottom colours for the time of day, then dimmed for the weather."""
        day_len = max(60.0, self.sunset - self.sunrise)
        if self.sunrise <= minute <= self.sunset:
            frac = (minute - self.sunrise) / day_len
            elev = math.sin(math.pi * frac)  # 0 at the horizon, 1 at noon
            edge = min(1.0, min(minute - self.sunrise, self.sunset - minute) / 45.0)
            top, bottom = (_lerp(DUSK[0], DAY[0], edge), _lerp(DUSK[1], DAY[1], edge))
            top = _lerp(top, DAY[0], elev * 0.4)
        else:
            dist = min(abs(minute - self.sunrise), abs(minute - self.sunset), abs(minute + 1440 - self.sunset), abs(minute - 1440 - self.sunrise))
            twilight = max(0.0, 1.0 - dist / 40.0)
            top, bottom = (_lerp(NIGHT[0], DUSK[0], twilight * 0.7), _lerp(NIGHT[1], DUSK[1], twilight * 0.8))
            elev = -1.0
        if self.cat in ("overcast", "fog", "drizzle", "rain", "snow"):
            f = 0.55 if self.cat in ("overcast", "fog", "snow") else 0.7
            top, bottom = _lerp(top, GRAY[0], f), _lerp(bottom, GRAY[1], f)
        elif self.cat == "thunder":
            top, bottom = _lerp(top, STORM[0], 0.85), _lerp(bottom, STORM[1], 0.85)
        return top, bottom, elev

    def _paint_sky(self, top, bottom):
        col = np.linspace(np.array(top, dtype=np.float32), np.array(bottom, dtype=np.float32), self.h)
        strip = Image.fromarray(col.astype(np.uint8)[:, None, :], "RGB").resize((self.w, self.h), Image.NEAREST)
        self.canvas.img.paste(strip, (0, 0))

    def _sun_pos(self, minute: float):
        frac = (minute - self.sunrise) / max(60.0, self.sunset - self.sunrise)
        x = self.w * (0.08 + 0.84 * frac)
        y = self.h * 0.92 - math.sin(math.pi * max(0.0, min(1.0, frac))) * self.h * 0.78
        return x, y

    def _cloud(self, d, x, y, w, color):
        h = w * 0.36
        d.rounded_rectangle([x - w / 2, y - h * 0.15, x + w / 2, y + h * 0.75], radius=int(h * 0.45), fill=color)
        for ox, oy, rr in ((-0.22, -0.05, 0.24), (0.06, -0.3, 0.32), (0.3, -0.08, 0.22)):
            cx, cy, r = x + w * ox, y + h * oy, w * rr
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    # --- frame -----------------------------------------------------------------------
    def draw(self, t: float) -> None:
        dt = min(0.5, max(0.0, t - self.last_t))
        self.last_t = t
        now = (self.t0 or time.time()) + t
        lt = time.localtime(now)
        minute = lt.tm_hour * 60 + lt.tm_min + lt.tm_sec / 60
        top, bottom, elev = self._sky(minute)
        self._paint_sky(top, bottom)
        d = self.canvas.draw
        img = self.canvas.img
        is_day = elev >= 0
        # stars
        if not is_day and self.cat in ("clear", "partly"):
            for x, y, ph, r in self.stars:
                tw = 0.55 + 0.45 * math.sin(t * 1.7 + ph)
                c = int(150 + 100 * tw)
                d.ellipse([x - r * tw, y - r * tw, x + r * tw, y + r * tw], fill=(c, c, min(255, c + 20)))
        # sun or moon (hidden behind heavy cloud, but a glow leaks through)
        if self.cat in ("clear", "partly", "fog", "snow") or (self.cat == "overcast" and is_day):
            if is_day:
                sx, sy = self._sun_pos(minute)
                rad = 22
                if self.cat == "overcast":
                    glow = _lerp(top, SUN, 0.35)
                    d.ellipse([sx - rad * 1.6, sy - rad * 1.6, sx + rad * 1.6, sy + rad * 1.6], fill=glow)
                else:
                    glow = _lerp(bottom, SUN, 0.28)
                    d.ellipse([sx - rad * 1.9, sy - rad * 1.9, sx + rad * 1.9, sy + rad * 1.9], fill=glow)
                    for i in range(12):
                        a = math.radians(t * 12 + i * 30)
                        d.line([(sx + math.cos(a) * rad * 1.35, sy + math.sin(a) * rad * 1.35), (sx + math.cos(a) * rad * 1.8, sy + math.sin(a) * rad * 1.8)], fill=SUN, width=3)
                    d.ellipse([sx - rad, sy - rad, sx + rad, sy + rad], fill=SUN)
            elif self.cat != "overcast":
                mx, my = self.w * 0.78, self.h * 0.28
                rad = 18
                d.ellipse([mx - rad, my - rad, mx + rad, my + rad], fill=MOON)
                ox, oy, rr = mx + rad * 0.5, my - rad * 0.3, rad * 0.9
                d.ellipse([ox - rr, oy - rr, ox + rr, oy + rr], fill=top)
        # clouds
        shade = {"clear": (238, 242, 248), "partly": (232, 236, 244)}.get(self.cat, (150, 156, 168) if self.cat != "thunder" else (72, 76, 88))
        if not is_day and self.cat in ("clear", "partly"):
            shade = (90, 96, 118)
        for c in sorted(self.clouds, key=lambda c: c["depth"]):
            c["x"] += (4 + self.wind * 0.9) * c["depth"] * dt
            if c["x"] - c["w"] > self.w:
                c["x"] = -c["w"]
                c["y"] = self.rng.uniform(6, self.h * 0.5)
            col = _lerp(top, shade, 0.45 + 0.55 * c["depth"])
            self._cloud(d, c["x"], c["y"], c["w"], col)
        # precipitation
        wind_dx = self.wind * 2.2
        for p in self.drops:
            p["y"] += p["speed"] * dt
            p["x"] += wind_dx * dt
            if p["y"] > self.h:
                p["y"] = -p["len"]
                p["x"] = self.rng.uniform(-40, self.w)
            if p["x"] > self.w + 10:
                p["x"] -= self.w + 20
            d.line([(p["x"], p["y"]), (p["x"] - wind_dx * 0.05, p["y"] + p["len"])], fill=RAIN, width=1)
        for f in self.flakes:
            f["y"] += f["speed"] * dt
            x = f["x"] + math.sin(t * 1.3 + f["phase"]) * 8 + self.wind * 0.3 * t % self.w
            if f["y"] > self.h:
                f["y"] = -4
                f["x"] = self.rng.uniform(0, self.w)
            x %= self.w
            d.ellipse([x - f["r"], f["y"] - f["r"], x + f["r"], f["y"] + f["r"]], fill=SNOW)
        # fog bands and lightning use a translucent layer
        layer = None
        if self.cat == "fog":
            layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            for i in range(5):
                y = self.h * (0.25 + 0.16 * i) + math.sin(t * 0.5 + i) * 6
                off = math.sin(t * 0.35 + i * 1.9) * 30
                ld.rounded_rectangle([-60 + off, y - 9, self.w + 60 + off, y + 9], radius=9, fill=(200, 206, 216, 90))
        if self.cat == "thunder":
            self.next_flash -= dt
            if self.next_flash <= 0:
                self.flash = 1.0
                self.next_flash = self.rng.uniform(3, 9)
                bx = self.rng.uniform(40, self.w - 40)
                self.bolt = [(bx, 0), (bx - 12, self.h * 0.35), (bx + 4, self.h * 0.38), (bx - 10, self.h * 0.72)]
            if self.flash > 0:
                layer = Image.new("RGBA", (self.w, self.h), (255, 255, 240, int(150 * self.flash)))
                if self.bolt and self.flash > 0.5:
                    ImageDraw.Draw(layer).line(self.bolt, fill=(255, 255, 210, 255), width=3)
                self.flash = 0.0 if self.flash < 0.3 else self.flash * 0.45
        if layer is not None:
            img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))
        self._overlay(lt)

    def _overlay(self, lt) -> None:
        """Time, temperature and date in dark pills, each inside a single key."""
        c = self.canvas
        d = c.draw
        pills = {7: (8, 20, 64, 52), 2: (10, 12, 62, 60), 12: (8, 24, 64, 48)}
        for key, (px0, py0, px1, py1) in pills.items():
            x0, y0, _, _ = c.key_box(key)
            box = (x0 + px0, y0 + py0, x0 + px1, y0 + py1)
            region = c.img.crop(box).convert("RGBA")
            shade = Image.new("RGBA", region.size, (0, 0, 0, 150))
            c.img.paste(Image.alpha_composite(region, shade).convert("RGB"), box[:2])
            d.rounded_rectangle(box, radius=8, outline=(255, 255, 255, 40), width=1)
        if self.twelve:
            hm = f"{lt.tm_hour % 12 or 12}:{lt.tm_min:02d}"
            c.key_text(7, hm, 24, FG)
            c.key_text(7, "PM" if lt.tm_hour >= 12 else "AM", 9, (200, 204, 212), where="b", pad=10, weight="semibold")
        else:
            c.key_text(7, f"{lt.tm_hour:02d}:{lt.tm_min:02d}", 24, FG)
        if self.cur:
            c.key_text(2, f"{round(self.cur['temp'])}°", 24, FG, where="t", pad=14)
            label = wx_icons.label(int(self.cur.get("code", 0)))
            c.key_text(2, label, fit_size(label, 44, 10, weight="semibold"), (210, 214, 222), where="b", pad=14, weight="semibold")
        else:
            c.key_text(2, "no data", 10, (210, 214, 222))
        c.key_text(12, time.strftime("%a %b %d", lt).replace(" 0", " "), 11, (220, 224, 232), weight="semibold")
