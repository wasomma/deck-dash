"""Tokyo night drive: an R34 Skyline holds the centre of the deck while Shibuya scrolls past.

Parallax layers, back to front: sky and stars; the far skyline with Tokyo Tower, Skytree and a
rail viaduct (a Yamanote train crosses now and then); neon-lit facades with vertical and
horizontal signs; the wet street with lamps, crossings and pedestrians; taxis in the far lane;
the car. Sign glyphs (MS Gothic) are decoration rather than text to read, so like the matrix
rain they may cross the bezels. About half the showings are rainy.
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..gfx import FONT_DIR
from .base import Scene

STREET_SPEED = 90.0  # px/s for the near layer; the car "drives" at this speed
FAR, MID, STREET = 0.28, 0.6, 1.0  # layer speeds as fractions of STREET_SPEED
CAR_LEN, WHEEL_R = 150, 13
TRAIN_SPEED = 220.0

NEON = [(255, 60, 180), (60, 230, 255), (255, 230, 60), (80, 255, 120), (255, 140, 40), (255, 70, 70), (200, 90, 255), (240, 240, 255)]
WORDS_JP = ["ラーメン", "居酒屋", "カラオケ", "寿司", "焼鳥", "薬局", "酒", "東京", "渋谷", "新宿", "ホテル", "喫茶", "パチンコ",
            "ゲーム", "バー", "銭湯", "餃子", "串カツ", "麻雀", "電気", "肉", "魚", "夜", "光", "銀座"]
WORDS_LATIN = ["RAMEN", "BAR", "HOTEL", "SUSHI", "KARAOKE", "GAME", "24H", "TOKYO", "SHIBUYA", "CAFE", "IZAKAYA", "NEON"]

BAYSIDE = (36, 96, 200)
BAYSIDE_DARK = (20, 56, 128)
BAYSIDE_LIGHT = (96, 156, 240)
GLASS = (26, 34, 52)
TIRE = (16, 16, 18)
RIM = (205, 205, 212)
ASPHALT = (26, 26, 34)
WARM = (255, 214, 150)
COOL = (170, 210, 255)
FACADES = [(30, 26, 44), (24, 30, 46), (38, 30, 38), (26, 34, 40), (34, 26, 48)]


def _font(size: int) -> tuple[ImageFont.ImageFont, bool]:
    """MS Gothic (or another Japanese font) for the signs; a Latin fallback keeps other machines working."""
    for fname in ("msgothic.ttc", "YuGothR.ttc", "meiryo.ttc"):
        path = os.path.join(FONT_DIR, fname)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size), True
            except OSError:
                continue
    return ImageFont.load_default(size=size), False


def _scale_rgb(im: Image.Image, factor: float) -> Image.Image:
    r, g, b, a = im.split()
    lut = [int(v * factor) for v in range(256)]
    return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))


def _scale_alpha(im: Image.Image, factor: float) -> Image.Image:
    im = im.copy()
    im.putalpha(im.split()[3].point([int(v * factor) for v in range(256)]))
    return im


def _rgba(color) -> tuple:
    return tuple(color) + (255,)


class TokyoScene(Scene):
    name = "tokyo"
    fps = 14.0

    def __init__(self, cfg, sources, seed=None):
        super().__init__(cfg, sources, seed)
        r = self.rng
        h = self.h
        self.rainy = r.random() < 0.5
        self.horizon = int(h * 0.52)
        self.viaduct_y = int(h * 0.43)
        self.street_y = int(h * 0.80)  # top of the sidewalk
        self.road_y = int(h * 0.825)  # top of the asphalt
        self.font, japanese = _font(12)
        self.words = WORDS_JP if japanese else WORDS_LATIN
        self.sky = self._sky()
        self.far, self.beacon = self._far_strip(2 * self.w)
        self.screen_variants = self._screen_variants()
        self.mid, self.signs, self.screens = self._mid_strip(3 * self.w)
        self.street = self._street_strip(2 * self.w)
        self.train_r = self._train_sprite()
        self.train_l = self.train_r.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        self.taxi_sprite = self._taxi_sprite()
        self.car_sprite, self.car_m = self._car_sprite()
        self.glow = self._glow((255, 40, 40), 44)
        self.beam = self._beam()
        self.train: dict | None = None
        self.next_train = r.uniform(3, 12)
        self.taxi: dict | None = None
        self.next_taxi = r.uniform(4, 10)

    # --- static layers ------------------------------------------------------------------

    def _sky(self) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h))
        d = ImageDraw.Draw(img)
        top, hz, bottom = (5, 6, 22), (54, 22, 76), (16, 10, 26)
        for y in range(self.h):
            if y < self.horizon:
                f = y / max(1, self.horizon)
                c = tuple(int(a + (b - a) * f) for a, b in zip(top, hz))
            else:
                f = (y - self.horizon) / max(1, self.h - self.horizon)
                c = tuple(int(a + (b - a) * f) for a, b in zip(hz, bottom))
            d.line([(0, y), (self.w, y)], fill=c)
        for _ in range(70):
            x, y = self.rng.uniform(0, self.w), self.rng.uniform(0, self.horizon * 0.7)
            v = self.rng.randint(120, 230)
            d.point((x, y), fill=(v, v, min(255, v + 20)))
        return img

    def _far_strip(self, W: int) -> tuple[Image.Image, tuple[int, int]]:
        r = self.rng
        h, hz = self.h, self.horizon
        im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        x = 0
        while x < W:
            bw = r.randint(26, 60)
            if x + bw > W - 26:
                bw = W - x
            bh = int(h * r.uniform(0.10, 0.34))
            top = hz - bh
            d.rectangle([x, top, x + bw - 1, hz + 40], fill=(16, 14, 32, 255))
            for wy in range(top + 4, hz - 2, 5):
                for wx in range(x + 3, x + bw - 3, 5):
                    if r.random() < 0.12:
                        c = (170, 140, 80) if r.random() < 0.6 else (100, 140, 190)
                        d.rectangle([wx, wy, wx + 1, wy + 1], fill=_rgba(c))
            if bh > h * 0.28 and r.random() < 0.6:
                d.point((x + bw // 2, top - 1), fill=(200, 40, 40, 255))
            x += bw + r.randint(0, 6)
        # Tokyo Tower: orange lattice with two lit decks and a beacon (drawn per frame)
        tx = int(W * r.uniform(0.08, 0.42))
        th = int(h * 0.46)
        ty = hz - th
        orange = (255, 110, 30, 255)
        d.polygon([(tx - 20, hz + 6), (tx - 3, ty), (tx + 3, ty), (tx + 20, hz + 6)], fill=(60, 26, 12, 255))
        for y in range(ty, hz + 6, 6):
            half = 3 + 17 * (y - ty) / th
            d.line([(tx - half, y), (tx + half, y)], fill=orange)
        d.line([(tx - 20, hz + 6), (tx - 3, ty)], fill=orange)
        d.line([(tx + 20, hz + 6), (tx + 3, ty)], fill=orange)
        deck = (255, 205, 130, 255)
        d.rectangle([tx - 13, ty + int(th * 0.55), tx + 13, ty + int(th * 0.55) + 7], fill=deck)
        d.rectangle([tx - 6, ty + int(th * 0.2), tx + 6, ty + int(th * 0.2) + 5], fill=deck)
        d.line([(tx, ty - 8), (tx, ty)], fill=(255, 160, 80, 255))
        # Skytree: pale blue needle with two observation rings
        sx = int(W * r.uniform(0.55, 0.92))
        sh = int(h * 0.50)
        sy = hz - sh
        d.polygon([(sx - 7, hz + 6), (sx - 1, sy), (sx + 1, sy), (sx + 7, hz + 6)], fill=(150, 200, 255, 200))
        d.line([(sx, sy), (sx, hz + 6)], fill=(225, 240, 255, 255))
        ring = (210, 235, 255, 255)
        d.rectangle([sx - 12, sy + int(sh * 0.36), sx + 12, sy + int(sh * 0.36) + 5], fill=ring)
        d.rectangle([sx - 8, sy + int(sh * 0.24), sx + 8, sy + int(sh * 0.24) + 3], fill=ring)
        d.point((sx, sy - 1), fill=(255, 60, 60, 255))
        # rail viaduct with railing posts and pillars (the pillars vanish behind the facades)
        v0, v1 = self.viaduct_y, int(h * 0.475)
        d.rectangle([0, v0, W, v1], fill=(44, 44, 58, 255))
        d.line([(0, v0), (W, v0)], fill=(80, 80, 96, 255))
        for px in range(0, W, 9):
            d.line([(px, v0 - 4), (px, v0)], fill=(70, 70, 86, 255))
        for px in range(20, W, 70):
            d.rectangle([px, v1, px + 5, self.street_y], fill=(34, 34, 46, 255))
        return im, (tx, ty - 9)

    def _screen_variants(self) -> list[Image.Image]:
        variants = []
        for c in [(255, 60, 200), (60, 220, 255), (255, 230, 80), (240, 240, 255)]:
            im = Image.new("RGBA", (36, 22), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, 35, 21], fill=(60, 60, 72, 255))
            d.rectangle([2, 2, 33, 19], fill=_rgba(c))
            for y in range(3, 19, 3):
                d.line([(2, y), (33, y)], fill=_rgba(tuple(int(v * 0.7) for v in c)))
            variants.append(im)
        return variants

    def _sign(self, word: str, color, vertical: bool) -> tuple[Image.Image, Image.Image, Image.Image]:
        """Bright sprite (with halo), dim sprite (flicker), and the wet-road reflection."""
        gs, pad, m = 12, 3, 6
        n = len(word)
        if vertical:
            bw, bh = gs + 2 * pad + 2, gs * n + 2 * pad + 2
        else:
            bw, bh = gs * n + 2 * pad + 2, gs + 2 * pad + 2
        im = Image.new("RGBA", (bw + 2 * m, bh + 2 * m), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([m, m, m + bw - 1, m + bh - 1], fill=(12, 8, 22, 255), outline=_rgba(color))
        for i, ch in enumerate(word):
            if vertical:
                cx, cy = m + bw / 2, m + pad + 1 + gs * i + gs / 2
            else:
                cx, cy = m + pad + 1 + gs * i + gs / 2, m + bh / 2
            d.text((cx, cy), ch, font=self.font, fill=_rgba(color), anchor="mm")
        halo = _scale_alpha(im.filter(ImageFilter.GaussianBlur(3)), 0.75)
        bright = Image.alpha_composite(halo, im)
        dim = _scale_alpha(_scale_rgb(im, 0.3), 0.9)
        refl = bright.transpose(Image.Transpose.FLIP_TOP_BOTTOM).resize((bright.width, int(bright.height * 1.3)))
        refl = _scale_alpha(refl.filter(ImageFilter.GaussianBlur(1.5)), 0.5 if self.rainy else 0.28)
        return bright, dim, refl

    def _shop_front(self, d: ImageDraw.ImageDraw, x: int, bw: int, sy: int) -> None:
        r = self.rng
        y0 = sy - 18
        kind = r.choice(["konbini", "izakaya", "ramen", "arcade", "bar", "dark"])
        if kind == "konbini":
            d.rectangle([x + 2, y0, x + bw - 3, sy], fill=(215, 255, 235, 255))
            d.rectangle([x + 2, y0, x + bw - 3, y0 + 3], fill=_rgba(r.choice([(0, 120, 200), (255, 120, 0), (0, 150, 70)])))
            d.rectangle([x + bw // 2 - 4, y0 + 6, x + bw // 2 + 4, sy], fill=(120, 170, 160, 255))
            d.rectangle([x + bw - 12, sy - 16, x + bw - 4, sy], fill=(225, 240, 255, 255))  # vending machine
            d.rectangle([x + bw - 11, sy - 14, x + bw - 5, sy - 6], fill=(90, 150, 230, 255))
        elif kind == "izakaya":
            d.rectangle([x + 2, y0, x + bw - 3, sy], fill=(110, 60, 30, 255))
            d.rectangle([x + 4, y0 + 5, x + bw - 5, sy - 2], fill=(255, 190, 110, 255))
            for lx in range(x + 8, x + bw - 6, 9):  # red lanterns under the awning
                d.line([(lx, y0), (lx, y0 + 3)], fill=(60, 40, 30, 255))
                d.ellipse([lx - 3, y0 + 3, lx + 3, y0 + 10], fill=(235, 45, 40, 255))
                d.point((lx, y0 + 6), fill=(255, 225, 130, 255))
        elif kind == "ramen":
            d.rectangle([x + 2, y0, x + bw - 3, sy], fill=(40, 24, 24, 255))
            d.rectangle([x + 4, y0 + 4, x + bw - 5, sy - 3], fill=(255, 220, 160, 255))
            for nx in range(x + 4, x + bw - 6, 6):  # noren curtain
                d.rectangle([nx, y0 + 1, nx + 3, y0 + 9], fill=(200, 30, 45, 255))
        elif kind == "arcade":
            d.rectangle([x + 2, y0, x + bw - 3, sy], fill=(200, 40, 160, 255))
            d.rectangle([x + 4, y0 + 3, x + bw - 5, sy - 3], fill=(60, 20, 70, 255))
            d.line([(x + 6, sy - 4), (x + bw - 7, y0 + 4)], fill=(60, 230, 255, 255), width=2)
        elif kind == "bar":
            d.rectangle([x + 2, y0, x + bw - 3, sy], fill=(18, 14, 26, 255))
            d.rectangle([x + 5, y0 + 6, x + bw - 6, y0 + 7], fill=_rgba(r.choice(NEON)))
            d.rectangle([x + bw // 2 - 3, y0 + 9, x + bw // 2 + 3, sy], fill=(70, 50, 40, 255))
        else:
            d.rectangle([x + 2, y0, x + bw - 3, sy], fill=(22, 20, 30, 255))
            d.rectangle([x + 6, y0 + 4, x + bw - 7, sy - 4], fill=(48, 52, 70, 255))
        d.rectangle([x + 1, y0 - 3, x + bw - 2, y0 - 1], fill=(70, 66, 84, 255))  # awning

    def _mid_strip(self, W: int) -> tuple[Image.Image, list[dict], list[dict]]:
        r = self.rng
        h, sy = self.h, self.street_y
        im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        signs: list[dict] = []
        screens: list[dict] = []
        x = 0
        while x < W:
            bw = r.randint(44, 96)
            if x + bw > W - 44:
                bw = W - x
            top = int(h * r.uniform(0.20, 0.55))
            col = r.choice(FACADES)
            d.rectangle([x, top, x + bw - 1, sy], fill=_rgba(col))
            d.line([(x, top), (x, sy)], fill=_rgba(tuple(min(255, c + 18) for c in col)))
            d.line([(x, top), (x + bw - 1, top)], fill=_rgba(tuple(min(255, c + 24) for c in col)))
            lit = r.uniform(0.2, 0.5)
            for wy in range(top + 5, sy - 24, 7):
                for wx in range(x + 4, x + bw - 6, 7):
                    if r.random() < lit:
                        c = WARM if r.random() < 0.65 else COOL
                        if r.random() < 0.3:
                            c = tuple(int(v * 0.6) for v in c)
                        d.rectangle([wx, wy, wx + 3, wy + 3], fill=_rgba(c))
            self._shop_front(d, x, bw, sy)
            for _ in range(r.choice([0, 1, 1, 2])):  # vertical neon signs on the facade
                word = r.choice(self.words)[:5]
                bright, dim, refl = self._sign(word, r.choice(NEON), vertical=True)
                if bright.width + 6 > bw:
                    continue
                y_lo, y_hi = max(top + 2, int(h * 0.24)), sy - 20 - bright.height
                if y_hi < y_lo:
                    continue
                signs.append({"x": r.randint(x + 3, x + bw - bright.width - 3), "y": r.randint(y_lo, y_hi), "w": bright.width,
                              "bright": bright, "dim": dim, "refl": refl, "flicker": r.random() < 0.15})
            if r.random() < 0.45:  # horizontal sign over the shop front
                word = r.choice(self.words)[:4]
                bright, dim, refl = self._sign(word, r.choice(NEON), vertical=False)
                if bright.width + 4 <= bw:
                    signs.append({"x": x + (bw - bright.width) // 2, "y": sy - 18 - bright.height + 4, "w": bright.width,
                                  "bright": bright, "dim": dim, "refl": refl, "flicker": r.random() < 0.15})
            if bw >= 56 and top < h * 0.38 and r.random() < 0.3:  # rooftop screen
                cx = x + bw // 2
                d.line([(cx - 10, top - 3), (cx - 10, top)], fill=(90, 90, 100, 255))
                d.line([(cx + 10, top - 3), (cx + 10, top)], fill=(90, 90, 100, 255))
                screens.append({"x": cx - 18, "y": top - 25, "phase": r.uniform(0, 4)})
            x += bw
        return im, signs, screens

    def _person(self, d: ImageDraw.ImageDraw, px: int, feet: int) -> None:
        ht = self.rng.randint(12, 16)
        c = (10, 10, 14, 255)
        d.ellipse([px - 2, feet - ht, px + 2, feet - ht + 4], fill=c)
        d.rectangle([px - 2, feet - ht + 4, px + 2, feet - 5], fill=c)
        d.line([(px - 1, feet - 5), (px - 2, feet)], fill=c)
        d.line([(px + 1, feet - 5), (px + 2, feet)], fill=c)

    def _street_strip(self, W: int) -> Image.Image:
        r = self.rng
        h, sy, ry = self.h, self.street_y, self.road_y
        im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([0, sy, W, ry - 1], fill=(62, 62, 76, 255))
        d.line([(0, ry - 1), (W, ry - 1)], fill=(100, 100, 114, 255))
        d.rectangle([0, ry, W, h], fill=_rgba(ASPHALT))
        ly = ry + int((h - ry) * 0.42)
        for dx in range(0, W, 44):
            d.rectangle([dx, ly, dx + 18, ly + 1], fill=(205, 195, 130, 255))
        crossings = [int(W * r.uniform(0.15, 0.35)), int(W * r.uniform(0.6, 0.85))]
        for cx in crossings:
            for stripe in range(cx, cx + 60, 16):
                d.rectangle([stripe, ry + 2, stripe + 8, h], fill=(150, 150, 158, 255))
        cones = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cones)
        lamps = list(range(r.randint(20, 80), W - 30, 150))
        for lx in lamps:
            cd.polygon([(lx - 3, sy - 46), (lx + 3, sy - 46), (lx + 34, h), (lx - 34, h)], fill=(255, 225, 170, 26))
        im = Image.alpha_composite(im, cones)
        d = ImageDraw.Draw(im)
        for lx in lamps:
            d.rectangle([lx - 1, sy - 48, lx, sy], fill=(84, 86, 98, 255))
            d.rectangle([lx - 5, sy - 50, lx + 5, sy - 47], fill=(190, 190, 200, 255))
            d.rectangle([lx - 3, sy - 47, lx + 3, sy - 45], fill=(255, 240, 200, 255))
        feet = ry - 2
        for cx in crossings:
            tx = cx - 10
            d.rectangle([tx, sy - 40, tx + 1, sy], fill=(84, 86, 98, 255))
            d.rectangle([tx - 3, sy - 54, tx + 4, sy - 40], fill=(30, 30, 36, 255))
            d.ellipse([tx - 1, sy - 52, tx + 2, sy - 49], fill=(70, 24, 24, 255))
            d.ellipse([tx - 1, sy - 48, tx + 2, sy - 45], fill=(80, 60, 20, 255))
            d.ellipse([tx - 1, sy - 44, tx + 2, sy - 41], fill=(70, 255, 130, 255))
            for _ in range(r.randint(2, 4)):
                self._person(d, cx + r.randint(-6, 60), feet)
        for _ in range(r.randint(3, 6)):
            self._person(d, r.randint(5, W - 5), feet)
        return im

    # --- sprites ------------------------------------------------------------------------

    @staticmethod
    def _train_sprite() -> Image.Image:
        im = Image.new("RGBA", (192, 18), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for car in range(3):
            x0 = car * 64
            d.rounded_rectangle([x0 + 1, 0, x0 + 62, 15], radius=3, fill=(196, 198, 206, 255))
            d.rectangle([x0 + 1, 4, x0 + 62, 6], fill=(0, 172, 100, 255))  # Yamanote green
            for wx in range(x0 + 6, x0 + 58, 12):
                d.rectangle([wx, 7, wx + 7, 12], fill=(255, 240, 200, 255))
            d.rectangle([x0 + 1, 15, x0 + 62, 17], fill=(40, 40, 48, 255))
        d.rectangle([0, 8, 1, 11], fill=(255, 60, 60, 255))
        d.rectangle([190, 8, 191, 11], fill=(255, 250, 220, 255))
        return im

    @staticmethod
    def _taxi_sprite() -> Image.Image:
        im = Image.new("RGBA", (72, 32), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        g = 31
        body = (28, 34, 92, 255)
        d.rectangle([1, g - 18, 70, g - 6], fill=body)
        d.polygon([(10, g - 18), (16, g - 29), (54, g - 29), (63, g - 18)], fill=body)
        d.polygon([(14, g - 19), (18, g - 27), (33, g - 27), (33, g - 19)], fill=(255, 230, 170, 255))
        d.polygon([(36, g - 19), (36, g - 27), (52, g - 27), (60, g - 19)], fill=(255, 230, 170, 255))
        d.rectangle([30, g - 32, 42, g - 29], fill=(255, 200, 70, 255))  # roof light
        d.rectangle([1, g - 14, 3, g - 10], fill=(255, 50, 50, 255))
        d.rectangle([68, g - 14, 70, g - 10], fill=(255, 245, 200, 255))
        for wx in (16, 56):
            d.ellipse([wx - 6, g - 12, wx + 6, g], fill=_rgba(TIRE))
            d.ellipse([wx - 3, g - 9, wx + 3, g - 3], fill=(150, 150, 160, 255))
        return im

    @staticmethod
    def _car_sprite() -> tuple[Image.Image, int]:
        """R34 Skyline GT-R, side view facing right, Bayside Blue. Wheels are drawn per frame."""
        m = 4
        im = Image.new("RGBA", (CAR_LEN + 2 * m, 52), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)

        def p(x, yu):  # x along the car, yu up from the wheel hubs' ground line
            return (x + m, 44 - yu)

        body = [(0, 9), (1, 21), (7, 26), (36, 27), (54, 39), (60, 41), (94, 41), (118, 28), (124, 26), (146, 22), (150, 17), (150, 10), (144, 8), (6, 8)]
        d.polygon([p(*q) for q in body], fill=_rgba(BAYSIDE))
        d.polygon([p(*q) for q in [(38, 28), (55, 38), (61, 38), (61, 28)]], fill=_rgba(GLASS))  # rear quarter glass
        d.polygon([p(*q) for q in [(65, 28), (65, 38), (92, 38), (114, 29)]], fill=_rgba(GLASS))  # door glass
        d.line([p(66, 37), p(90, 37)], fill=(70, 90, 120, 255))
        d.rectangle([p(8, 34), p(12, 27)], fill=_rgba(BAYSIDE_DARK))  # wing stanchions
        d.rectangle([p(26, 34), p(30, 27)], fill=_rgba(BAYSIDE_DARK))
        d.rectangle([p(1, 37), p(38, 34)], fill=_rgba(BAYSIDE))  # the big rear wing
        d.line([p(1, 37), p(38, 37)], fill=_rgba(BAYSIDE_LIGHT))
        d.rectangle([p(10, 11), p(140, 8)], fill=_rgba(BAYSIDE_DARK))  # side skirt
        d.line([p(65, 11), p(65, 27)], fill=_rgba(BAYSIDE_DARK))  # door shut line
        d.rectangle([p(78, 23), p(84, 21)], fill=(14, 20, 40, 255))  # handle
        d.rectangle([p(114, 34), p(119, 30)], fill=_rgba(BAYSIDE))  # mirror
        d.line([p(60, 41), p(94, 41)], fill=_rgba(BAYSIDE_LIGHT))  # roof highlight
        d.line([p(124, 26), p(146, 22)], fill=_rgba(BAYSIDE_LIGHT))  # bonnet highlight
        d.rectangle([p(0, 24), p(5, 10)], fill=(18, 22, 36, 255))  # rear face
        for yu in (20, 14):  # the quad round tail lights, two visible from the side
            d.ellipse([p(0.5, yu + 2.5), p(5.5, yu - 2.5)], fill=(255, 40, 40, 255))
            d.ellipse([p(2, yu + 1), p(4, yu - 1)], fill=(255, 160, 160, 255))
        d.polygon([p(*q) for q in [(141, 22), (150, 18), (150, 14), (139, 17)]], fill=(255, 250, 220, 255))  # headlight
        d.rectangle([p(138, 10), p(150, 8)], fill=(14, 14, 18, 255))  # front lip
        for wx in (30, 120):  # wheel arches (upper half; the tyre covers the rest)
            d.arc([p(wx - 15, 24), p(wx + 15, -6)], 180, 360, fill=_rgba(BAYSIDE_DARK), width=2)
        return im, m

    @staticmethod
    def _glow(color, size: int) -> Image.Image:
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        c = size / 2
        for i in range(10, 0, -1):
            rr = c * i / 10
            d.ellipse([c - rr, c - rr, c + rr, c + rr], fill=tuple(color) + (int(6 + 9 * (10 - i)),))
        return im

    @staticmethod
    def _beam() -> Image.Image:
        im = Image.new("RGBA", (120, 44), (0, 0, 0, 0))
        ImageDraw.Draw(im).polygon([(0, 4), (120, 0), (120, 44), (0, 10)], fill=(255, 240, 200, 38))
        return im.filter(ImageFilter.GaussianBlur(2))

    # --- per frame ----------------------------------------------------------------------

    def _blit(self, strip: Image.Image, off: float) -> None:
        """Paste a wrapping strip at scroll offset ``off`` (one or two crops)."""
        W = strip.width
        off = int(off) % W
        end = off + self.w
        if end <= W:
            part = strip.crop((off, 0, end, strip.height))
            self.canvas.img.paste(part, (0, 0), part)
        else:
            a = strip.crop((off, 0, W, strip.height))
            self.canvas.img.paste(a, (0, 0), a)
            b = strip.crop((0, 0, end - W, strip.height))
            self.canvas.img.paste(b, (W - off, 0), b)

    def _screen_x(self, x: float, off: float, W: int, spr_w: int) -> int | None:
        """Screen x of a layer-space sprite, or None when it is off screen."""
        sx = (x - off) % W
        if sx < self.w:
            return int(sx)
        if sx + spr_w > W:
            return int(sx - W)
        return None

    def _train(self, t: float) -> None:
        if self.train is None and t >= self.next_train:
            dirn = 1 if self.rng.random() < 0.5 else -1
            self.train = {"dir": dirn, "x0": -200 if dirn > 0 else self.w + 8, "t0": t}
        if self.train is None:
            return
        x = self.train["x0"] + self.train["dir"] * TRAIN_SPEED * (t - self.train["t0"])
        if x > self.w + 8 or x < -200:
            self.train = None
            self.next_train = t + self.rng.uniform(20, 40)
            return
        spr = self.train_r if self.train["dir"] > 0 else self.train_l
        self.canvas.img.paste(spr, (int(x), self.viaduct_y - 18), spr)

    def _taxi(self, t: float, ground: int) -> None:
        if self.taxi is None and t >= self.next_taxi:
            overtaking = self.rng.random() < 0.6
            self.taxi = {"v": 54.0 if overtaking else -45.0, "x0": -80 if overtaking else self.w + 8, "t0": t}
        if self.taxi is None:
            return
        x = self.taxi["x0"] + self.taxi["v"] * (t - self.taxi["t0"])
        if x > self.w + 8 or x < -80:
            self.taxi = None
            self.next_taxi = t + self.rng.uniform(12, 25)
            return
        spr = self.taxi_sprite
        self.canvas.img.paste(spr, (int(x), ground - 9 - spr.height + 1), spr)

    def _car(self, t: float, ground: int) -> None:
        img, d, m = self.canvas.img, self.canvas.draw, self.car_m
        bob = int(round(math.sin(t * 7.0) * 0.8))
        car_x = (self.w - CAR_LEN) // 2 - m
        top = ground - 48 + bob
        img.paste(self.glow, (car_x + m + 3 - 28, top + 27 - 22), self.glow)
        img.paste(self.beam, (car_x + m + 149, top + 22), self.beam)
        img.paste(self.car_sprite, (car_x, top), self.car_sprite)
        theta = (STREET_SPEED * t / WHEEL_R) % (2 * math.pi)
        for wx in (30, 120):
            cx, cy = car_x + m + wx, top + 35
            d.ellipse([cx - WHEEL_R, cy - WHEEL_R, cx + WHEEL_R, cy + WHEEL_R], fill=TIRE)
            d.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], outline=RIM, width=2)
            for k in range(5):
                a = theta + k * 2 * math.pi / 5
                d.line([(cx, cy), (cx + 7 * math.cos(a), cy + 7 * math.sin(a))], fill=RIM, width=2)
            d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=RIM)

    def draw(self, t: float) -> None:
        img, d = self.canvas.img, self.canvas.draw
        img.paste(self.sky, (0, 0))
        off_far, off_mid, off_st = STREET_SPEED * FAR * t, STREET_SPEED * MID * t, STREET_SPEED * STREET * t
        self._blit(self.far, off_far)
        bx, by = self.beacon
        sx = (bx - off_far) % self.far.width
        if sx < self.w and (t % 1.2) < 0.6:
            d.ellipse([sx - 2, by - 2, sx + 2, by + 2], fill=(255, 50, 50))
        self._train(t)
        self._blit(self.mid, off_mid)
        W = self.mid.width
        visible = []
        for s in self.signs:
            sx = self._screen_x(s["x"], off_mid, W, s["w"])
            if sx is None:
                continue
            spr = s["dim"] if (s["flicker"] and self.rng.random() < 0.35) else s["bright"]
            img.paste(spr, (sx, s["y"]), spr)
            visible.append((sx, s))
        for sc in self.screens:
            sx = self._screen_x(sc["x"], off_mid, W, 36)
            if sx is not None:
                spr = self.screen_variants[int(t * 0.6 + sc["phase"]) % 4]
                img.paste(spr, (sx, sc["y"]), spr)
        self._blit(self.street, off_st)
        for sx, s in visible:
            img.paste(s["refl"], (sx, self.road_y + 1), s["refl"])
        ground = self.h - 3
        self._taxi(t, ground)
        self._car(t, ground)
        if self.rainy:
            for _ in range(60):
                x, y = self.rng.uniform(0, self.w), self.rng.uniform(0, self.h)
                d.line([(x, y), (x - 3, y + 11)], fill=(120, 135, 175))
