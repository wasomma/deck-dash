"""Procedural, animated weather glyphs (RGBA) that read at any size from 12 px up.

``t`` is wall-clock seconds; every glyph is a pure function of (code, is_day, t).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

SUN = (255, 200, 60)
MOON = (222, 226, 238)
CLOUD = (206, 212, 222)
CLOUD_DARK = (118, 126, 140)
RAIN = (96, 166, 255)
SNOW = (240, 244, 255)
BOLT = (255, 232, 96)
FOG = (176, 182, 194)

LABELS = {
    "clear": "Clear",
    "partly": "Partly",
    "overcast": "Cloudy",
    "fog": "Fog",
    "drizzle": "Drizzle",
    "rain": "Rain",
    "snow": "Snow",
    "thunder": "Storm",
}


def category(code: int) -> str:
    """Map a WMO weather code (Open-Meteo) to a glyph category."""
    if code in (0, 1):
        return "clear"
    if code == 2:
        return "partly"
    if code == 3:
        return "overcast"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "thunder"
    return "overcast"


def label(code: int) -> str:
    return LABELS[category(code)]


def icon(size: int, code: int, is_day: bool = True, t: float = 0.0) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = float(size)
    cat = category(code)
    if cat == "clear":
        if is_day:
            _sun(d, s * 0.5, s * 0.5, s * 0.26, t, s)
        else:
            _moon(d, s * 0.5, s * 0.5, s * 0.3)
    elif cat == "partly":
        if is_day:
            _sun(d, s * 0.38, s * 0.36, s * 0.19, t, s)
        else:
            _moon(d, s * 0.38, s * 0.36, s * 0.2)
        _cloud(d, s * 0.58, s * 0.64, s * 0.38, CLOUD)
    elif cat == "overcast":
        _cloud(d, s * 0.42, s * 0.46, s * 0.38, CLOUD_DARK)
        _cloud(d, s * 0.56, s * 0.6, s * 0.4, CLOUD)
    elif cat == "fog":
        _cloud(d, s * 0.5, s * 0.36, s * 0.38, CLOUD_DARK)
        _fog(d, s, t)
    elif cat in ("drizzle", "rain"):
        _cloud(d, s * 0.5, s * 0.36, s * 0.42, CLOUD_DARK)
        _rain(d, s, t, 3 if cat == "drizzle" else 5)
    elif cat == "snow":
        _cloud(d, s * 0.5, s * 0.36, s * 0.42, CLOUD)
        _snow(d, s, t)
    else:  # thunder
        _cloud(d, s * 0.5, s * 0.34, s * 0.44, CLOUD_DARK)
        _rain(d, s, t, 3)
        phase = t % 3.0
        _bolt(d, s, on=phase < 0.18 or 0.32 < phase < 0.42)
    return img


def _sun(d, cx, cy, r, t, s):
    w = max(1, int(s / 24))
    for i in range(8):
        a = math.radians(t * 20 + i * 45)
        x0, y0 = cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35
        x1, y1 = cx + math.cos(a) * r * 1.75, cy + math.sin(a) * r * 1.75
        d.line([(x0, y0), (x1, y1)], fill=SUN, width=w)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SUN)


def _moon(d, cx, cy, r):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=MOON)
    ox, oy, rr = cx + r * 0.45, cy - r * 0.35, r * 0.85
    d.ellipse([ox - rr, oy - rr, ox + rr, oy + rr], fill=(0, 0, 0, 0))


def _cloud(d, cx, cy, w, color):
    h = w * 0.32
    d.rounded_rectangle(
        [int(cx - w / 2), int(cy - h * 0.2), int(cx + w / 2), int(cy + h * 0.8)],
        radius=int(h * 0.5),
        fill=color,
    )
    r1 = w * 0.24
    x, y = cx - w * 0.2, cy - h * 0.1
    d.ellipse([x - r1, y - r1, x + r1, y + r1], fill=color)
    r2 = w * 0.3
    x, y = cx + w * 0.08, cy - h * 0.35
    d.ellipse([x - r2, y - r2, x + r2, y + r2], fill=color)


def _rain(d, s, t, n):
    w = max(1, int(s / 30))
    for i in range(n):
        x = s * (0.28 + 0.44 * (i / max(1, n - 1)))
        ph = (t * 1.6 + i * 0.29) % 1.0
        y = s * 0.55 + ph * s * 0.32
        d.line([(x, y), (x - s * 0.05, y + s * 0.13)], fill=RAIN, width=w)


def _snow(d, s, t):
    r = max(1.0, s / 28)
    for i in range(4):
        x = s * (0.28 + 0.44 * i / 3) + math.sin(t * 2 + i) * s * 0.03
        ph = (t * 0.7 + i * 0.25) % 1.0
        y = s * 0.55 + ph * s * 0.38
        d.ellipse([x - r, y - r, x + r, y + r], fill=SNOW)


def _fog(d, s, t):
    w = max(1, int(s / 24))
    for i in range(3):
        y = s * (0.6 + 0.13 * i)
        off = math.sin(t * 1.2 + i * 1.7) * s * 0.06
        d.line([(s * 0.18 + off, y), (s * 0.82 + off, y)], fill=FOG, width=w)


def _bolt(d, s, on):
    if not on:
        return
    pts = [
        (s * 0.52, s * 0.5), (s * 0.4, s * 0.72), (s * 0.5, s * 0.72),
        (s * 0.44, s * 0.94), (s * 0.62, s * 0.66), (s * 0.52, s * 0.66), (s * 0.6, s * 0.5),
    ]
    d.polygon(pts, fill=BOLT)
