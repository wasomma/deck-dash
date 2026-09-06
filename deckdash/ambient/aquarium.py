"""Pixel aquarium: fish cross the bezels, bubbles rise, weeds sway, a crab patrols the sand."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from .base import Scene

FISH_COLORS = [(255, 140, 40), (250, 220, 60), (80, 160, 255), (240, 80, 80), (180, 110, 255), (90, 220, 130), (255, 190, 120)]
SAND = (188, 170, 122)
SAND_DARK = (150, 132, 90)
WEED = (40, 150, 80)
WEED_DARK = (28, 110, 60)
CRAB = (230, 90, 50)
BUBBLE = (170, 210, 255)


class AquariumScene(Scene):
    name = "aquarium"
    fps = 8.0

    def __init__(self, cfg, sources, seed=None):
        super().__init__(cfg, sources, seed)
        r = self.rng
        self.sand_h = 16
        self.water_h = self.h - self.sand_h
        self.pebbles = [(r.uniform(0, self.w), r.uniform(self.water_h + 3, self.h - 3), r.uniform(1.5, 3.5)) for _ in range(22)]
        self.bg = self._background()
        self.fish = []
        for i in range(6):
            size = r.uniform(10, 22)
            self.fish.append({
                "x": r.uniform(0, self.w), "y": r.uniform(20, self.water_h - 30), "size": size,
                "vx": r.uniform(18, 42) * (1 if r.random() < 0.5 else -1) * (14 / size) ** 0.5,
                "color": FISH_COLORS[i % len(FISH_COLORS)], "phase": r.uniform(0, 6.28), "bob": r.uniform(2, 6),
            })
        self.weeds = [{"x": r.uniform(10, self.w - 10), "h": r.uniform(40, 95), "phase": r.uniform(0, 6.28), "segs": r.randint(5, 8)} for _ in range(5)]
        self.bubbles = [self._bubble(start_anywhere=True) for _ in range(9)]
        self.crab = {"x": r.uniform(40, self.w - 40), "dir": 1, "next_turn": r.uniform(3, 8)}
        self.last_t = 0.0

    def _background(self) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h))
        d = ImageDraw.Draw(img)
        top, bottom = (14, 52, 110), (4, 20, 56)
        for y in range(self.water_h):
            f = y / max(1, self.water_h - 1)
            d.line([(0, y), (self.w, y)], fill=tuple(int(a + (b - a) * f) for a, b in zip(top, bottom)))
        d.rectangle([0, self.water_h, self.w, self.h], fill=SAND)
        for x, y, rad in self.pebbles:
            d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=SAND_DARK)
        return img

    def _bubble(self, start_anywhere: bool = False) -> dict:
        r = self.rng
        return {"x": r.uniform(8, self.w - 8), "y": r.uniform(0, self.water_h) if start_anywhere else self.water_h - 2,
                "r": r.uniform(1.5, 3.5), "speed": r.uniform(16, 30), "phase": r.uniform(0, 6.28)}

    def _fish(self, d, f, t):
        s = f["size"]
        x = f["x"]
        y = f["y"] + math.sin(t * 2.0 + f["phase"]) * f["bob"]
        dirn = 1 if f["vx"] > 0 else -1
        col = f["color"]
        dark = tuple(int(c * 0.6) for c in col)
        # tail (flaps), body, dorsal fin, eye
        flap = math.sin(t * 9 + f["phase"]) * s * 0.25
        tail_x = x - dirn * s * 0.95
        d.polygon([(x - dirn * s * 0.6, y), (tail_x - dirn * s * 0.35, y - s * 0.5 + flap), (tail_x - dirn * s * 0.35, y + s * 0.5 + flap)], fill=dark)
        d.ellipse([x - s, y - s * 0.5, x + s, y + s * 0.5], fill=col)
        d.polygon([(x - s * 0.3, y - s * 0.45), (x + s * 0.2, y - s * 0.45), (x - s * 0.05, y - s * 0.85)], fill=dark)
        ex = x + dirn * s * 0.55
        d.ellipse([ex - s * 0.13, y - s * 0.22 - s * 0.13, ex + s * 0.13, y - s * 0.22 + s * 0.13], fill=(250, 250, 250))
        d.ellipse([ex + dirn * s * 0.04 - s * 0.06, y - s * 0.22 - s * 0.06, ex + dirn * s * 0.04 + s * 0.06, y - s * 0.22 + s * 0.06], fill=(10, 10, 20))

    def _weed(self, d, w, t):
        x, y = w["x"], self.water_h + 2
        seg = w["h"] / w["segs"]
        pts = [(x, y)]
        for i in range(1, w["segs"] + 1):
            sway = math.sin(t * 1.4 + w["phase"] + i * 0.6) * (3 + i * 1.6)
            pts.append((x + sway, y - seg * i))
        d.line(pts, fill=WEED_DARK, width=5)
        d.line(pts, fill=WEED, width=3)

    def _crab(self, d, t):
        c = self.crab
        x, y = c["x"], self.water_h - 4
        walk = math.sin(t * 8) * 2
        for i in (-1, 1):
            for j in range(3):
                lx = x + i * (6 + j * 4)
                d.line([(x + i * 4, y), (lx, y + 4 + (walk if (j + (i > 0)) % 2 else -walk))], fill=CRAB, width=2)
        d.ellipse([x - 9, y - 6, x + 9, y + 4], fill=CRAB)
        d.ellipse([x - 5, y - 9, x - 2, y - 6], fill=(255, 255, 255))
        d.ellipse([x + 2, y - 9, x + 5, y - 6], fill=(255, 255, 255))
        d.ellipse([x - 4, y - 8, x - 3, y - 7], fill=(0, 0, 0))
        d.ellipse([x + 3, y - 8, x + 4, y - 7], fill=(0, 0, 0))
        cl = 1 if math.sin(t * 3) > 0 else 0
        d.line([(x - 9, y - 3), (x - 15, y - 8 - cl * 2)], fill=CRAB, width=3)
        d.line([(x + 9, y - 3), (x + 15, y - 8 - cl * 2)], fill=CRAB, width=3)

    def draw(self, t: float) -> None:
        dt = min(0.5, max(0.0, t - self.last_t))
        self.last_t = t
        self.canvas.img.paste(self.bg, (0, 0))
        d = self.canvas.draw
        for w in self.weeds:
            self._weed(d, w, t)
        for f in self.fish:
            f["x"] += f["vx"] * dt
            margin = f["size"] * 1.6
            if f["x"] > self.w + margin and f["vx"] > 0:
                f["vx"] = -abs(f["vx"])
                f["y"] = self.rng.uniform(20, self.water_h - 30)
            elif f["x"] < -margin and f["vx"] < 0:
                f["vx"] = abs(f["vx"])
                f["y"] = self.rng.uniform(20, self.water_h - 30)
            self._fish(d, f, t)
        for b in self.bubbles:
            b["y"] -= b["speed"] * dt
            bx = b["x"] + math.sin(t * 3 + b["phase"]) * 2
            if b["y"] < -4:
                b.update(self._bubble())
                continue
            d.ellipse([bx - b["r"], b["y"] - b["r"], bx + b["r"], b["y"] + b["r"]], outline=BUBBLE, width=1)
        c = self.crab
        c["x"] += 12 * c["dir"] * dt
        c["next_turn"] -= dt
        if c["x"] < 24 or c["x"] > self.w - 24 or c["next_turn"] <= 0:
            c["dir"] *= -1
            c["x"] = min(max(c["x"], 24), self.w - 24)
            c["next_turn"] = self.rng.uniform(3, 9)
        self._crab(d, t)
