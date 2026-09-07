"""Weather as ambient: the whole deck becomes the view out of a window, sky above land, with the
time and temperature on top. Sky colour follows the real sunrise/sunset, the sun and the moon rise
and set behind the hills, clouds drift with the wind, and rain, snow, fog and lightning follow the
current conditions.

Layers, back to front: sky gradient; stars, the moon at tonight's real phase, and the sun; cirrus;
the drifting clouds; birds; then the land -- two ridges receding into haze, the hills, and the
field with its fence and grass, with the treeline, a lone tree and a barn on top of them. Every
piece of land is a silhouette mask built once in ``__init__`` and recoloured each frame, hazed
toward the current sky colour by how far away it is, so the landscape is lit by whatever the sky
is doing and there is no second palette to keep in step. Precipitation, fog and lightning go in
front of all of it, and the clock, temperature and date pills on top.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from .. import wx_icons
from ..gfx import FG, KEY, fit_size
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

GROUND_DAY = (54, 86, 54)
GROUND_DUSK = (58, 44, 48)
GROUND_NIGHT = (24, 28, 42)
GROUND_SNOW = (214, 220, 232)
GROUND_WET = (48, 54, 60)
GROUND_STORM = (22, 24, 32)
WINDOW = (255, 186, 92)

# Days between two new moons, and a new moon to count from (2000-01-06 18:14 UTC).
SYNODIC = 29.530588853
NEW_MOON = 947182440.0


def _lerp(a, b, f):
    return tuple(int(x + (y - x) * f) for x, y in zip(a, b))


def _minute_of(iso: str, fallback: float) -> float:
    try:
        return int(iso[11:13]) * 60 + int(iso[14:16])
    except (ValueError, TypeError, IndexError):
        return fallback


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _haze(sky):
    """What distance fades the land toward: the sky, pulled back off its own saturation.

    Lerping straight at the sky turns the hills orange at sunset, which reads as mud rather than
    as distance; keeping the value and dropping a third of the colour keeps them hills.
    """
    lum = int(0.299 * sky[0] + 0.587 * sky[1] + 0.114 * sky[2])
    return _lerp(sky, (lum, lum, lum), 0.34)


class WeatherScene(Scene):
    name = "weather"
    fps = 14.0

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
        self.horizon = 2 * (KEY + self.gap) + 20.0  # where the near land starts, inside the bottom row
        r = self.rng
        self.stars = [(r.uniform(0, self.w), r.uniform(0, self.horizon - 10), r.uniform(0, 6.28), r.uniform(0.6, 1.6)) for _ in range(45)]
        n_clouds = {"clear": 1, "partly": 3, "overcast": 6, "fog": 2, "drizzle": 5, "rain": 6, "snow": 5, "thunder": 6}[self.cat]
        self.clouds = []
        for i in range(n_clouds):
            depth = r.uniform(0.5, 1.0)
            self.clouds.append({"x": r.uniform(-80, self.w), "y": r.uniform(6, self.h * 0.42), "w": r.uniform(70, 150) * depth, "depth": depth})
        # thin high cirrus, so a clear sky is not a bare gradient
        self.cirrus = [{"x": r.uniform(-40, self.w), "y": r.uniform(self.h * 0.06, self.h * 0.44), "w": r.uniform(90, 190), "h": r.uniform(2.0, 4.5), "a": r.randint(26, 54)} for _ in range(5 if self.cat in ("clear", "partly") else 0)]
        self.birds = [{"x": r.uniform(0, self.w), "y": r.uniform(self.h * 0.18, self.h * 0.5), "s": r.uniform(2.2, 3.6), "v": r.uniform(7, 16), "ph": r.uniform(0, 6.28)} for _ in range(4 if self.cat in ("clear", "partly") else 0)]
        n_drops = {"drizzle": 45, "rain": 110, "thunder": 80}.get(self.cat, 0)
        self.drops = [{"x": r.uniform(0, self.w), "y": r.uniform(0, self.h), "len": r.uniform(6, 14), "speed": r.uniform(220, 340)} for _ in range(n_drops)]
        self.flakes = [{"x": r.uniform(0, self.w), "y": r.uniform(0, self.h), "r": r.uniform(1.2, 3.0), "speed": r.uniform(18, 40), "phase": r.uniform(0, 6.28)} for _ in range(40 if self.cat == "snow" else 0)]
        self.splashes = []
        self.flash = 0.0
        self.next_flash = r.uniform(3, 8)
        self.bolt = None
        self.shoot = None
        self.next_shoot = r.uniform(6, 30)
        self.last_t = 0.0
        self._build_land()

    # --- land ------------------------------------------------------------------------
    def _ridge(self, base: float, amp: float, waves) -> list[tuple[float, float]]:
        """A smooth skyline: a few sines of different periods, phase-shifted per scene."""
        phases = [self.rng.uniform(0, 6.283) for _ in waves]
        pts = []
        for x in range(self.w + 1):
            u = x / self.w
            y = base
            for (freq, weight), ph in zip(waves, phases):
                y -= amp * weight * math.sin(u * freq + ph)
            pts.append((float(x), y))
        return pts

    def _fill_below(self, pts) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        m = Image.new("L", (self.w, self.h), 0)
        d = ImageDraw.Draw(m)
        d.polygon(pts + [(float(self.w), float(self.h)), (0.0, float(self.h))], fill=255)
        return m, d

    @staticmethod
    def _conifer(d, x: float, base: float, h: float) -> None:
        w = h * 0.34
        d.rectangle([x - 1, base - h * 0.25, x + 1, base + 2], fill=255)
        d.polygon([(x, base - h), (x - w, base - h * 0.42), (x + w, base - h * 0.42)], fill=255)
        d.polygon([(x, base - h * 0.72), (x - w * 1.35, base + 2), (x + w * 1.35, base + 2)], fill=255)

    @staticmethod
    def _lone_tree(d, x: float, base: float, h: float) -> None:
        """A broadleaf tree ``h`` tall: trunk for the lower two fifths, canopy above it."""
        trunk = h * 0.42
        d.polygon([(x - 2.6, base + 1), (x - 1.1, base - trunk), (x + 1.1, base - trunk), (x + 2.6, base + 1)], fill=255)
        d.line([(x, base - trunk * 0.78), (x - h * 0.15, base - trunk * 1.16)], fill=255, width=2)
        d.line([(x, base - trunk * 0.78), (x + h * 0.15, base - trunk * 1.1)], fill=255, width=2)
        cy = base - h * 0.72
        for ox, oy, rr in ((-0.19, 0.10, 0.19), (0.19, 0.08, 0.18), (0.0, -0.13, 0.22), (-0.02, 0.17, 0.20)):
            cx, cyy, r = x + h * ox, cy + h * oy, h * rr
            d.ellipse([cx - r, cyy - r, cx + r, cyy + r], fill=255)

    def _barn(self, d, x: float, base: float, w: float, h: float) -> None:
        d.rectangle([x, base - h, x + w, base + 2], fill=255)
        d.polygon([(x - 3, base - h + 1), (x + w * 0.5, base - h - h * 0.62), (x + w + 3, base - h + 1)], fill=255)
        sx = x + w + 5  # silo
        d.rectangle([sx, base - h * 1.15, sx + 7, base + 2], fill=255)
        d.ellipse([sx - 1, base - h * 1.15 - 5, sx + 8, base - h * 1.15 + 4], fill=255)
        self.window = (x + w * 0.26, base - h * 0.72, x + w * 0.26 + 4, base - h * 0.72 + 4)

    def _build_land(self) -> None:
        """Four silhouette masks, far to near. Recoloured per frame; the shapes never change.

        The bands are placed off the key rows, not off the canvas: the 24 px of bezel between the
        middle and bottom rows is invisible, and a treeline or a roof left in it simply disappears.
        So the ridges live in the lower half of the middle row and everything with a shape to it --
        trees, the barn, the fence -- stays inside the bottom row.
        """
        r = self.rng
        row_h = KEY + self.gap
        mid_bot = row_h + KEY  # bottom edge of the middle row
        low = 2 * row_h  # top edge of the bottom row
        far_pts = self._ridge(mid_bot - 22, 26.0, ((2.1, 0.5), (4.9, 0.32), (10.3, 0.18)))
        second_pts = self._ridge(mid_bot - 6, 11.0, ((2.9, 0.5), (6.3, 0.32), (13.1, 0.18)))
        mid_pts = self._ridge(low + 20, 6.0, ((3.3, 0.55), (7.7, 0.3), (16.1, 0.15)))
        near_pts = self._ridge(low + 40, 4.0, ((4.6, 0.6), (11.7, 0.4)))
        far, _ = self._fill_below(far_pts)
        second, _ = self._fill_below(second_pts)

        mid, _ = self._fill_below(mid_pts)
        crest = [p[1] for p in mid_pts]
        near, nd = self._fill_below(near_pts)
        ncrest = [p[1] for p in near_pts]

        # Trees and buildings get a mask of their own rather than joining the band they stand on:
        # sharing it, they only showed as a scalloped edge against the band behind, and under snow
        # they vanished into white ground. Painted darker than any band, they read in every weather.
        props = Image.new("L", (self.w, self.h), 0)
        pd = ImageDraw.Draw(props)
        tx = int(1.5 * (KEY + self.gap))  # centre of key 11; a landmark in a bezel gap is invisible
        bx = int(3 * (KEY + self.gap) + KEY * 0.26)  # the barn and its silo, inside key 13
        x = 4.0  # the treeline: clumps of conifers with clearings between them
        while x < self.w - 4:
            if abs(x - tx) > 24 and not (bx - 14 < x < bx + 42):
                self._conifer(pd, x, crest[int(x)] + 1, min(r.uniform(8, 18), crest[int(x)] - low - 2))
            x += r.uniform(5, 11) if r.random() < 0.72 else r.uniform(22, 48)
        self._barn(pd, bx, crest[bx] + 1, 20.0, 12.0)
        self._lone_tree(pd, tx, ncrest[tx] + 5, 34.0)  # rooted in the field, so it reads as near

        for x in range(9, self.w - 6, 31):  # fence posts on the crest, two rails between them
            nd.rectangle([x - 1, ncrest[x] - 10, x + 1, ncrest[x] + 2], fill=255)
        rail = min(ncrest[9:-6]) - 7
        nd.rectangle([0, rail, self.w, rail + 1], fill=255)
        nd.rectangle([0, rail + 4, self.w, rail + 5], fill=255)
        for _ in range(80):  # tufts along the field edge
            x = r.uniform(0, self.w)
            base = ncrest[int(x)] + 1
            nd.line([(x, base), (x + r.uniform(-1.5, 1.5), base - r.uniform(2, 5))], fill=255, width=1)

        detail = Image.new("L", (self.w, self.h), 0)  # scattered clumps, so the field is not flat
        dd = ImageDraw.Draw(detail)
        for _ in range(55):
            x = r.uniform(0, self.w)
            y = r.uniform(ncrest[int(x)] + 5, self.h)
            for k in range(r.randint(2, 3)):
                dx = r.uniform(-2.0, 2.0)
                dd.line([(x + dx, y), (x + dx * 1.5, y - r.uniform(1.5, 3.5))], fill=255, width=1)

        self.land = [far, second, mid, near]
        self.land_haze = (1.0, 0.62, 0.3, 0.0)  # how much of the distance haze each band takes
        self.land_dark = (0.0, 0.0, 0.12, 0.3)
        self.props = props
        self.rims = [ImageChops.subtract(m, ImageChops.offset(m, 0, 4)) for m in (far, second, mid)]
        self.detail = detail
        self.caps = [ImageChops.subtract(m, ImageChops.offset(m, 0, 3)) for m in self.land] if self.cat == "snow" else None  # terrain only
        self.near_crest = ncrest
        self.blades = [{"x": r.uniform(0, self.w), "h": r.uniform(6, 13), "ph": r.uniform(0, 6.28), "lean": r.uniform(-0.3, 0.3)} for _ in range(14)]

    # --- sky -------------------------------------------------------------------------
    def _sky(self, minute: float):
        """Top/bottom sky, sun elevation, ground colour and how far the haze reaches."""
        day_len = max(60.0, self.sunset - self.sunrise)
        if self.sunrise <= minute <= self.sunset:
            frac = (minute - self.sunrise) / day_len
            elev = math.sin(math.pi * frac)  # 0 at the horizon, 1 at noon
            edge = min(1.0, min(minute - self.sunrise, self.sunset - minute) / 45.0)
            top, bottom = (_lerp(DUSK[0], DAY[0], edge), _lerp(DUSK[1], DAY[1], edge))
            top = _lerp(top, DAY[0], elev * 0.4)
            ground = _lerp(GROUND_DUSK, GROUND_DAY, edge)
        else:
            dist = min(abs(minute - self.sunrise), abs(minute - self.sunset), abs(minute + 1440 - self.sunset), abs(minute - 1440 - self.sunrise))
            twilight = max(0.0, 1.0 - dist / 40.0)
            top, bottom = (_lerp(NIGHT[0], DUSK[0], twilight * 0.7), _lerp(NIGHT[1], DUSK[1], twilight * 0.8))
            ground = _lerp(GROUND_NIGHT, GROUND_DUSK, twilight * 0.75)
            elev = -1.0
        haze = 0.44
        if self.cat in ("overcast", "fog", "drizzle", "rain", "snow"):
            f = 0.55 if self.cat in ("overcast", "fog", "snow") else 0.7
            top, bottom = _lerp(top, GRAY[0], f), _lerp(bottom, GRAY[1], f)
            ground = _lerp(ground, GROUND_WET, 0.5 if self.cat != "snow" else 0.3)
        elif self.cat == "thunder":
            top, bottom = _lerp(top, STORM[0], 0.85), _lerp(bottom, STORM[1], 0.85)
            ground = _lerp(ground, GROUND_STORM, 0.8)
        if self.cat == "snow":
            ground = _lerp(ground, GROUND_SNOW, 0.72 if elev >= 0 else 0.4)
        elif self.cat == "fog":
            haze = 0.85
        return top, bottom, elev, ground, haze

    def _paint_sky(self, top, bottom):
        col = np.linspace(np.array(top, dtype=np.float32), np.array(bottom, dtype=np.float32), self.h)
        strip = Image.fromarray(col.astype(np.uint8)[:, None, :], "RGB").resize((self.w, self.h), Image.NEAREST)
        self.canvas.img.paste(strip, (0, 0))

    def _sky_at(self, y: float, top, bottom):
        return _lerp(top, bottom, _clamp01(y / self.h))

    def _arc_pos(self, frac: float):
        """Rise and set behind the hills: both ends of the arc sit just under the horizon."""
        frac = _clamp01(frac)
        x = self.w * (0.06 + 0.88 * frac)
        y = self.horizon + 6 - math.sin(math.pi * frac) * (self.horizon - self.h * 0.1)
        return x, y

    def _sun_pos(self, minute: float):
        return self._arc_pos((minute - self.sunrise) / max(60.0, self.sunset - self.sunrise))

    def _moon_pos(self, minute: float):
        night = (self.sunrise + 1440 - self.sunset) % 1440 or 660.0
        past = (minute - self.sunset) % 1440
        return self._arc_pos(past / night)

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
        top, bottom, elev, ground, haze = self._sky(minute)
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
            self._shooting_star(d, dt)
        # sun or moon (hidden behind heavy cloud, but a glow leaks through)
        if self.cat in ("clear", "partly", "fog", "snow") or (self.cat == "overcast" and is_day):
            if is_day:
                sx, sy = self._sun_pos(minute)
                rad = 22
                if self.cat == "overcast":
                    glow = _lerp(top, SUN, 0.35)
                    d.ellipse([sx - rad * 1.6, sy - rad * 1.6, sx + rad * 1.6, sy + rad * 1.6], fill=glow)
                else:
                    base = self._sky_at(sy, top, bottom)
                    for scale, f in ((3.0, 0.12), (2.4, 0.2), (1.9, 0.3)):  # soft falloff into the sky
                        d.ellipse([sx - rad * scale, sy - rad * scale, sx + rad * scale, sy + rad * scale], fill=_lerp(base, SUN, f))
                    for i in range(12):
                        a = math.radians(t * 12 + i * 30)
                        d.line([(sx + math.cos(a) * rad * 1.35, sy + math.sin(a) * rad * 1.35), (sx + math.cos(a) * rad * 1.8, sy + math.sin(a) * rad * 1.8)], fill=SUN, width=3)
                    d.ellipse([sx - rad, sy - rad, sx + rad, sy + rad], fill=SUN)
            elif self.cat != "overcast":
                self._moon(d, minute, now, top, bottom)
        # cirrus, then clouds
        self._cirrus(t, 1.0 if is_day else 0.3)
        shade = {"clear": (238, 242, 248), "partly": (232, 236, 244)}.get(self.cat, (150, 156, 168) if self.cat != "thunder" else (72, 76, 88))
        if not is_day and self.cat in ("clear", "partly"):
            shade = (90, 96, 118)
        for c in sorted(self.clouds, key=lambda c: c["depth"]):
            c["x"] += (4 + self.wind * 0.9) * c["depth"] * dt
            if c["x"] - c["w"] > self.w:
                c["x"] = -c["w"]
                c["y"] = self.rng.uniform(6, self.h * 0.42)
            col = _lerp(top, shade, 0.45 + 0.55 * c["depth"])
            self._cloud(d, c["x"], c["y"], c["w"], col)
        # birds, still in the sky and so still occluded by the land
        for b in self.birds:
            b["x"] = (b["x"] + (b["v"] + self.wind * 0.4) * dt) % (self.w + 24)
            s, flap = b["s"], t * 6.0 + b["ph"]
            dy = s * 0.55 * math.sin(flap)
            col = _lerp(self._sky_at(b["y"], top, bottom), (24, 26, 34), 0.7)
            d.line([(b["x"] - 12 - s, b["y"] + dy), (b["x"] - 12, b["y"] - s * 0.4), (b["x"] - 12 + s, b["y"] + dy)], fill=col, width=1)
        # the land: one mask per band, each hazed toward the sky by how far away it is
        hz = _haze(bottom)
        cols = [_lerp(_lerp(ground, (0, 0, 0), dk), hz, haze * hw) for hw, dk in zip(self.land_haze, self.land_dark)]
        for mask, col in zip(self.land, cols):
            img.paste(col, (0, 0), mask)
        img.paste(_lerp(cols[-1], (0, 0, 0), 0.18), (0, 0), self.detail)
        rim_f = 0.4 if is_day else 0.22  # air catching the light along each crest
        for rim, col in zip(self.rims, cols):
            img.paste(_lerp(col, hz, rim_f), (0, 0), rim)
        img.paste(_lerp(cols[-1], (0, 0, 0), 0.34), (0, 0), self.props)
        if self.caps is not None:  # snow lying along each terrain crest
            for cap, col in zip(self.caps, cols):
                img.paste(_lerp(col, SNOW, 0.55 if is_day else 0.3), (0, 0), cap)
        if not is_day or self.cat == "thunder":  # the barn window, lit and slowly flickering
            wx0, wy0, wx1, wy1 = self.window
            d.rectangle([wx0, wy0, wx1, wy1], fill=_lerp(cols[2], WINDOW, 0.55 + 0.35 * math.sin(t * 0.9)))
        self._grass(d, t, cols[-1])
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
        self._splash(d, dt, ground)
        for f in self.flakes:
            f["y"] += f["speed"] * dt
            x = f["x"] + math.sin(t * 1.3 + f["phase"]) * 8 + self.wind * 0.3 * t % self.w
            if f["y"] > self.h:
                f["y"] = -4
                f["x"] = self.rng.uniform(0, self.w)
            x %= self.w
            d.ellipse([x - f["r"], f["y"] - f["r"], x + f["r"], f["y"] + f["r"]], fill=SNOW)
        # fog banks and lightning use a translucent layer
        layer = None
        if self.cat == "fog":
            layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            for i in range(5):  # lying on the land, thinning upward
                y = self.h * (0.42 + 0.15 * i) + math.sin(t * 0.5 + i) * 5
                off = math.sin(t * 0.35 + i * 1.9) * 30
                ld.rounded_rectangle([-60 + off, y - 9, self.w + 60 + off, y + 9], radius=9, fill=(200, 206, 216, 60 + 18 * i))
        if self.cat == "thunder":
            self.next_flash -= dt
            if self.next_flash <= 0:
                self.flash = 1.0
                self.next_flash = self.rng.uniform(3, 9)
                bx = self.rng.uniform(40, self.w - 40)
                self.bolt = [(bx, 0), (bx - 12, self.h * 0.3), (bx + 4, self.h * 0.34), (bx - 10, self.horizon - 6)]
            if self.flash > 0:
                layer = Image.new("RGBA", (self.w, self.h), (255, 255, 240, int(150 * self.flash)))
                if self.bolt and self.flash > 0.5:
                    ImageDraw.Draw(layer).line(self.bolt, fill=(255, 255, 210, 255), width=3)
                self.flash = 0.0 if self.flash < 0.3 else self.flash * 0.45
        if layer is not None:
            img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"), (0, 0))
        self._overlay(lt)

    # --- pieces ----------------------------------------------------------------------
    def _moon(self, d, minute: float, now: float, top, bottom) -> None:
        """The moon on its own arc, lit to tonight's real phase."""
        mx, my = self._moon_pos(minute)
        rad = 16
        sky = self._sky_at(my, top, bottom)
        d.ellipse([mx - rad, my - rad, mx + rad, my + rad], fill=MOON)
        phase = ((now - NEW_MOON) / 86400.0 / SYNODIC) % 1.0
        lit = (1.0 - math.cos(2 * math.pi * phase)) / 2  # 0 new, 1 full
        off = 2.0 * rad * lit
        sx = mx - off if phase < 0.5 else mx + off  # waxing is lit on the right
        d.ellipse([sx - rad, my - rad, sx + rad, my + rad], fill=sky)

    def _cirrus(self, t: float, lit: float) -> None:
        if not self.cirrus:
            return
        y0 = int(min(c["y"] - c["h"] for c in self.cirrus)) - 4
        y1 = int(max(c["y"] + c["h"] for c in self.cirrus)) + 4
        box = (0, max(0, y0), self.w, min(self.h, y1))
        strip = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
        sd = ImageDraw.Draw(strip)
        for c in self.cirrus:
            x = (c["x"] + (2.0 + self.wind * 0.3) * t) % (self.w + c["w"] * 2) - c["w"]
            y = c["y"] - box[1]
            for i in range(7):  # overlapping discs thinning to nothing at both ends
                f = i / 6
                taper = math.sin(math.pi * (0.1 + 0.8 * f))
                ew, eh = c["w"] * 0.2, c["h"] * taper
                cx = x + f * c["w"]
                sd.ellipse([cx - ew, y - eh, cx + ew, y + eh], fill=(255, 255, 255, int(c["a"] * taper * lit)))
        region = self.canvas.img.crop(box).convert("RGBA")
        self.canvas.img.paste(Image.alpha_composite(region, strip).convert("RGB"), box[:2])

    def _shooting_star(self, d, dt: float) -> None:
        if self.shoot is None:
            self.next_shoot -= dt
            if self.next_shoot <= 0:
                r = self.rng
                self.shoot = {"x": r.uniform(0, self.w * 0.8), "y": r.uniform(8, self.h * 0.35), "vx": r.uniform(150, 240), "vy": r.uniform(50, 90), "life": 0.75}
                self.next_shoot = r.uniform(14, 45)
            return
        s = self.shoot
        s["life"] -= dt
        if s["life"] <= 0 or s["x"] > self.w or s["y"] > self.horizon:
            self.shoot = None
            return
        s["x"] += s["vx"] * dt
        s["y"] += s["vy"] * dt
        a = _clamp01(s["life"] / 0.75)
        c = int(120 + 135 * a)
        d.line([(s["x"], s["y"]), (s["x"] - s["vx"] * 0.06, s["y"] - s["vy"] * 0.06)], fill=(c, c, min(255, c + 15)), width=1)

    def _grass(self, d, t: float, near_col) -> None:
        """Foreground blades leaning in the wind: the one thing that moves down here."""
        col = _lerp(near_col, (0, 0, 0), 0.35)
        sway = 1.5 + self.wind * 0.22
        for b in self.blades:
            x, h = b["x"], b["h"]
            lean = b["lean"] + math.sin(t * 1.6 + b["ph"]) * 0.12
            tip = x + lean * h * 2 + math.sin(t * 2.3 + b["ph"]) * sway
            d.line([(x, self.h), (x + (tip - x) * 0.45, self.h - h * 0.6), (tip, self.h - h)], fill=col, width=1)

    def _splash(self, d, dt: float, ground) -> None:
        """Rain landing on the field: short ticks that pop and fade along the near crest."""
        if not self.drops:
            return
        for s in self.splashes:
            s["life"] -= dt
        self.splashes = [s for s in self.splashes if s["life"] > 0]
        want = 3 if self.cat == "drizzle" else 7
        while len(self.splashes) < want:
            x = self.rng.uniform(0, self.w)
            y = self.near_crest[int(x)] + self.rng.uniform(0, 24)
            self.splashes.append({"x": x, "y": min(y, self.h - 2), "life": self.rng.uniform(0.1, 0.35)})
        for s in self.splashes:
            r = 1.0 + 2.6 * (1.0 - s["life"] / 0.35)
            col = _lerp(ground, RAIN, 0.35 + 0.4 * _clamp01(s["life"] / 0.35))
            d.line([(s["x"] - r, s["y"]), (s["x"] + r, s["y"])], fill=col, width=1)

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
