"""News ticker: one marquee across a whole row; zoom = five headlines, one per column, press to open.

The marquee is the one place text deliberately crosses bezels: the motion carries the eye
over the gaps, and the bezel calibration (Phase 3) is what makes it look continuous.
"""

from __future__ import annotations

import logging
import time
import webbrowser

from PIL import Image, ImageDraw

from ..canvas import Canvas
from ..gfx import BG, DIM, FG, KEY, fmt_duration, font, new_key, text, text_width
from .base import Tile
from .ci import wrap_text

log = logging.getLogger(__name__)

DEFAULT_COLORS = [(255, 102, 0), (255, 90, 120), (224, 57, 62), (82, 160, 255), (72, 214, 120)]
TITLE_SIZE = 24
TAG_SIZE = 20
SEP_W = 64


def parse_color(s: str | None, fallback):
    if not s:
        return fallback
    s = s.lstrip("#")
    if len(s) == 6:
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return fallback
    return fallback


def _age(now: float, at: float) -> str:
    if not at:
        return ""
    d = fmt_duration(max(0, now - at))
    return d.split(" ")[0]


class Strip:
    """The rendered marquee: the headlines laid out once, followed by a copy of their start so
    the scroll wraps seamlessly; ``ranges`` holds the x extent of every headline."""

    def __init__(self, items: list[dict], colors: dict, view_w: int):
        self.items = items
        self.view_w = view_w
        widths = []
        for it in items:
            tag_w = text_width(it["source"], TAG_SIZE, "semibold") + 10
            title_w = text_width(it["title"], TITLE_SIZE, "bold")
            widths.append(tag_w + title_w + SEP_W)
        self.content_w = max(1, sum(widths))
        self.cycle = self.content_w
        content = Image.new("RGB", (self.content_w, KEY), BG)
        d = ImageDraw.Draw(content)
        self.ranges: list[tuple[int, int]] = []
        x = 0
        for it, w in zip(items, widths):
            start = x
            col = colors.get(it["source"], DIM)
            text(d, (x, KEY / 2 + 1), it["source"], TAG_SIZE, col, weight="semibold", anchor="lm")
            x += text_width(it["source"], TAG_SIZE, "semibold") + 10
            text(d, (x, KEY / 2), it["title"], TITLE_SIZE, FG, weight="bold", anchor="lm")
            x += text_width(it["title"], TITLE_SIZE, "bold")
            sx = x + SEP_W / 2
            d.ellipse([sx - 3, KEY / 2 - 3, sx + 3, KEY / 2 + 3], fill=DIM)
            x += SEP_W
            self.ranges.append((start, x))
        self.img = Image.new("RGB", (self.content_w + view_w, KEY), BG)
        px = 0
        while px < self.img.width:  # content, then as much of its head as the window needs
            self.img.paste(content, (px, 0))
            px += self.content_w

    def frame(self, pos: float) -> Image.Image:
        x = int(pos) % self.cycle
        return self.img.crop((x, 0, x + self.view_w, KEY))

    def index_at(self, pos: float) -> int:
        """Headline under the middle of the window: the one being read."""
        x = (int(pos) % self.cycle + self.view_w // 2) % self.content_w
        for i, (a, b) in enumerate(self.ranges):
            if a <= x < b:
                return i
        return 0


class NewsTile(Tile):
    name = "news"
    zoomable = True
    width = 5
    zoom_refresh = 5.0

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        n = cfg.get("news", {})
        dk = cfg.get("deck", {})
        self.refresh = 1.0 / float(n.get("fps", dk.get("tick_hz", 10)))
        self.speed = float(n.get("speed_px", 60))
        self.colors = {}
        for i, f in enumerate(n.get("feeds", [])):
            self.colors[f.get("name", "?")] = parse_color(f.get("color"), DEFAULT_COLORS[i % len(DEFAULT_COLORS)])
        self.strip: Strip | None = None
        self.strip_stamp = None
        self.t0 = time.time()
        self.canvas = Canvas(self.gap, cols=self.width, rows=1)
        self.zoom_items: list[dict] = []

    # --- marquee ---------------------------------------------------------------------
    def _pos(self, now: float) -> float:
        return (now - self.t0) * self.speed

    def _ensure_strip(self, now: float) -> Strip | None:
        st = self.sources["news"].state
        items = st.get("items") or []
        stamp = st.get("fetched")
        if not items:
            return self.strip
        if self.strip is None:
            self.strip = Strip(items, self.colors, self.canvas.w)
            self.strip_stamp = stamp
            self.t0 = now
        elif stamp != self.strip_stamp:
            # New headlines: swap them in at the wrap point so the scroll never jumps.
            pos = self._pos(now) % self.strip.cycle
            if pos < self.speed * self.refresh * 2:
                self.strip = Strip(items, self.colors, self.canvas.w)
                self.strip_stamp = stamp
                self.t0 = now
        return self.strip

    def render(self, now):
        return self.render_span(now)[0]

    def render_span(self, now):
        strip = self._ensure_strip(now)
        if strip is None:
            src = self.sources["news"]
            first = self.placeholder("news", src.error or "loading")
            return [first] + [new_key() for _ in range(self.width - 1)]
        band = strip.frame(self._pos(now))
        self.canvas.img.paste(band, (0, 0))
        return self.canvas.slice()

    # --- zoom ------------------------------------------------------------------------
    def on_press(self) -> None:
        strip = self.strip
        if strip is None:
            self.zoom_items = []
            return
        i = strip.index_at(self._pos(time.time()))
        items = strip.items
        self.zoom_items = [items[(i + k) % len(items)] for k in range(min(5, len(items)))]

    def on_zoom_press(self, key: int) -> None:
        col = key % 5
        if col < len(self.zoom_items):
            url = self.zoom_items[col]["url"]
            log.info("opening %s", url)
            try:
                webbrowser.open(url)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not open browser: %s", exc)

    def render_zoom(self, now):
        """Five columns, one headline each: source and age on top, the title wrapped below."""
        c = Canvas(self.gap)
        items = self.zoom_items
        if not items:
            c.key_text(7, "no headlines", 13, DIM)
            return c.slice()
        for col, it in enumerate(items[:5]):
            color = self.colors.get(it["source"], DIM)
            c.key_text(col, it["source"], 11, color, where="tl", pad=4, weight="semibold")
            c.key_text(col, _age(now, it["at"]), 10, DIM, where="tr", pad=4, weight="semibold")
            lines = wrap_text(it["title"], KEY - 8, 13, weight="semibold", max_lines=11)
            slots = [(col, 24), (col, 39), (col, 54), (5 + col, 12), (5 + col, 27), (5 + col, 42), (5 + col, 57),
                     (10 + col, 12), (10 + col, 27), (10 + col, 42), (10 + col, 57)]
            for line, (key, y) in zip(lines, slots):
                x0, y0, _, _ = c.key_box(key)
                text(c.draw, (x0 + 4, y0 + y), line, 13, FG, weight="semibold", anchor="lm")
        return c.slice()
