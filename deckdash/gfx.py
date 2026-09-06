"""Drawing helpers shared by tiles, zoom views and ambient scenes."""

from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

KEY = 72

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_FILES = {
    "bold": "segoeuib.ttf",
    "semibold": "seguisb.ttf",
    "regular": "segoeui.ttf",
    "mono": "consola.ttf",
}

BG = (10, 12, 16)
FG = (235, 238, 242)
DIM = (128, 136, 148)
TRACK = (36, 40, 48)
GREEN = (72, 214, 120)
AMBER = (255, 184, 48)
RED = (255, 84, 84)
BLUE = (82, 160, 255)
CYAN = (64, 220, 230)
PURPLE = (190, 120, 255)
WARM = (255, 150, 90)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


@lru_cache(maxsize=128)
def font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    path = os.path.join(FONT_DIR, FONT_FILES.get(weight, FONT_FILES["bold"]))
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size=size)


def new_key(bg=BG) -> Image.Image:
    return Image.new("RGB", (KEY, KEY), bg)


def text(draw: ImageDraw.ImageDraw, xy, s: str, size: int = 14, fill=FG, weight: str = "bold", anchor: str = "mm") -> None:
    draw.text(xy, s, font=font(size, weight), fill=fill, anchor=anchor)


def text_width(s: str, size: int, weight: str = "bold") -> int:
    left, _, right, _ = font(size, weight).getbbox(s)
    return right - left


def fit_size(s: str, max_w: int, size: int, min_size: int = 7, weight: str = "bold") -> int:
    """Largest font size <= ``size`` at which ``s`` fits in ``max_w`` pixels."""
    while size > min_size and text_width(s, size, weight) > max_w:
        size -= 1
    return size


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_color(c1, c2, t: float):
    t = clamp(t)
    return tuple(int(round(lerp(a, b, t))) for a, b in zip(c1, c2))


def heat(frac: float):
    """0 -> green, 0.5 -> amber, 1 -> red."""
    frac = clamp(frac)
    if frac < 0.5:
        return lerp_color(GREEN, AMBER, frac * 2)
    return lerp_color(AMBER, RED, (frac - 0.5) * 2)


def arc_gauge(draw, center, radius: float, width: int, frac: float, color, track=TRACK, sweep: float = 270) -> None:
    cx, cy = center
    box = [cx - radius, cy - radius, cx + radius, cy + radius]
    start = 90 + (360 - sweep) / 2  # opening centred at the bottom
    draw.arc(box, start, start + sweep, fill=track, width=width)
    frac = clamp(frac)
    if frac > 0:
        draw.arc(box, start, start + sweep * frac, fill=color, width=width)


def hbar(draw, box, frac: float, color, track=TRACK) -> None:
    x0, y0, x1, y1 = box
    h = y1 - y0
    draw.rounded_rectangle(box, radius=h // 2, fill=track)
    w = int((x1 - x0) * clamp(frac))
    if w > 0:
        draw.rounded_rectangle([x0, y0, x0 + max(w, h), y1], radius=h // 2, fill=color)


def vbar(draw, box, frac: float, color, track=TRACK) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, fill=track)
    h = int((y1 - y0) * clamp(frac))
    if h > 0:
        draw.rectangle([x0, y1 - h, x1, y1], fill=color)


def sparkline(draw, box, values, color, vmax: float | None = None, vmin: float = 0.0, fill=None, width: int = 1) -> None:
    x0, y0, x1, y1 = box
    n = len(values)
    if n < 2:
        return
    hi = vmax if vmax is not None else (max(values) or 1.0)
    hi = max(hi, vmin + 1e-9)
    pts = []
    for i, v in enumerate(values):
        x = x0 + (x1 - x0) * i / (n - 1)
        y = y1 - (y1 - y0) * clamp((v - vmin) / (hi - vmin))
        pts.append((x, y))
    if fill is not None:
        draw.polygon([(x0, y1)] + pts + [(x1, y1)], fill=fill)
    draw.line(pts, fill=color, width=width)


def fmt_rate(bps: float) -> str:
    """Bytes per second, compact: 12.4M, 830K, 12B."""
    for suffix, div in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if bps >= div:
            v = bps / div
            return f"{v:.1f}{suffix}" if v < 10 else f"{v:.0f}{suffix}"
    return f"{bps:.0f}B"


def fmt_bytes(n: float) -> str:
    for suffix, div in (("T", 1 << 40), ("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if n >= div:
            v = n / div
            return f"{v:.1f}{suffix}" if v < 10 else f"{v:.0f}{suffix}"
    return f"{n:.0f}B"


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
