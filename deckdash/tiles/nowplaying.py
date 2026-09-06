"""Now playing: overlays another tile's key while music plays (see ``[layout] overlays``)."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from ..canvas import Canvas
from ..gfx import BG, DIM, FG, GREEN, KEY, TRACK, fit_size, hbar, new_key, text, text_width
from ..sources.media import is_active
from .base import Tile
from .ci import wrap_text

ACCENT = (120, 220, 160)


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _position(st: dict, now: float) -> float:
    pos = float(st.get("pos", 0.0))
    if st.get("status") == "Playing" and st.get("updated"):
        pos += max(0.0, now - float(st["updated"]))
    dur = float(st.get("dur", 0.0))
    return min(pos, dur) if dur > 0 else pos


class NowPlayingTile(Tile):
    name = "nowplaying"
    zoomable = True
    zoom_refresh = 0.5

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        self.anim = 1.0 / float(cfg.get("deck", {}).get("anim_fps", 8))
        self.grace = float(cfg.get("nowplaying", {}).get("paused_grace_seconds", 120))
        self.ignore = [str(s) for s in cfg.get("nowplaying", {}).get("ignore_apps", [])]
        self._art_cache: dict = {}
        self.refresh = 2.0

    # --- helpers ---------------------------------------------------------------------
    def _state(self) -> dict:
        src = self.sources.get("media")
        return src.state if src is not None else {}

    def _active(self, st: dict, now: float) -> bool:
        return is_active(st, now, self.grace, self.ignore)

    def active(self, now: float) -> bool:
        return self._active(self._state(), now)

    def _art(self, st: dict, size: int) -> Image.Image | None:
        art = st.get("art")
        if art is None:
            return None
        key = (st.get("art_key"), size)
        if key not in self._art_cache:
            if len(self._art_cache) > 4:
                self._art_cache.clear()
            self._art_cache[key] = art.resize((size, size), Image.LANCZOS)
        return self._art_cache[key]

    def _scroll_text(self, d, y: float, s: str, size: int, color, now: float, width: int = KEY - 8, weight: str = "bold") -> None:
        """Text that fits is centred; longer text slides back and forth inside the key."""
        w = text_width(s, size, weight)
        if w <= width:
            text(d, (KEY / 2, y), s, size, color, weight=weight)
            return
        span = w - width
        off = (0.5 - 0.5 * math.cos(now * 0.9)) * span
        text(d, (4 - off, y), s, size, color, weight=weight, anchor="lm")

    # --- board -----------------------------------------------------------------------
    def render(self, now):
        st = self._state()
        if not self._active(st, now):
            self.refresh = 2.0
            return self.placeholder("music", "nothing playing")
        self.refresh = self.anim if st.get("status") == "Playing" else 1.0
        img = new_key()
        art = self._art(st, KEY)
        if art is not None:
            img.paste(art, (0, 0))
            shade = Image.new("L", (KEY, KEY), 0)
            sd = ImageDraw.Draw(shade)
            for y in range(34, KEY):
                sd.line([(0, y), (KEY, y)], fill=int(200 * (y - 34) / (KEY - 34)))
            img.paste(Image.new("RGB", (KEY, KEY), BG), (0, 0), shade)
        d = ImageDraw.Draw(img)
        if st.get("status") == "Paused":
            d.rectangle([52, 4, 57, 16], fill=FG)
            d.rectangle([61, 4, 66, 16], fill=FG)
        self._scroll_text(d, 47, st.get("title") or "?", 12, FG, now)
        artist = st.get("artist") or ""
        if artist:
            text(d, (KEY / 2, 59), artist, fit_size(artist, KEY - 8, 9, weight="semibold"), (210, 214, 222), weight="semibold")
        dur = float(st.get("dur", 0.0))
        if dur > 0:
            hbar(d, (6, 66, KEY - 6, 69), _position(st, now) / dur, ACCENT, track=(60, 64, 72))
        return img

    # --- zoom ------------------------------------------------------------------------
    def render_zoom(self, now):
        st = self._state()
        c = Canvas(self.gap)
        if not self._active(st, now):
            c.key_text(7, "nothing playing", 12, DIM)
            return c.slice()
        d = c.draw
        bx0, by0, bx1, by1 = c.span_box(0, 6)
        art = self._art(st, bx1 - bx0)
        if art is not None:
            c.img.paste(art, (bx0, by0))
        else:
            d.rectangle((bx0, by0, bx1, by1), fill=TRACK)
            c.key_text(0, "no art", 11, DIM)
        title_lines = wrap_text(st.get("title") or "?", 58, 13, max_lines=6)
        slots = [(2, 6), (2, 24), (2, 42), (3, 6), (3, 24), (3, 42)]
        for line, (key, pad) in zip(title_lines, slots):
            c.key_text(key, line, 13, FG, where="t", pad=pad, weight="semibold")
        for j, line in enumerate(wrap_text(st.get("artist") or "", 58, 12, max_lines=3)):
            c.key_text(4, line, 12, ACCENT, where="t", pad=6 + 17 * j, weight="semibold")
        for j, line in enumerate(wrap_text(st.get("album") or "", 58, 10, max_lines=3)):
            c.key_text(7, line, 10, DIM, where="t", pad=6 + 14 * j, weight="semibold")
        app = (st.get("app") or "").split("!")[0].split("_")[0][:14]
        c.key_text(8, st.get("status", ""), 12, GREEN if st.get("status") == "Playing" else DIM, where="t", pad=8, weight="semibold")
        c.key_text(8, app, fit_size(app, 62, 10, weight="semibold"), DIM, where="b", pad=8, weight="semibold")
        c.key_text(9, "controls", 10, DIM, where="t", pad=8, weight="semibold")
        c.key_text(9, "bottom row", 10, DIM, where="t", pad=22, weight="semibold")
        rx0, ry0, rx1, ry1 = c.row_box(2)
        dur = float(st.get("dur", 0.0))
        pos = _position(st, now)
        hbar(d, (rx0 + 8, ry0 + 30, rx1 - 8, ry0 + 40), (pos / dur) if dur > 0 else 0.0, ACCENT, track=(40, 44, 52))
        c.key_text(10, _clock(pos), 13, FG, where="b", pad=6, weight="semibold")
        c.key_text(14, _clock(dur) if dur > 0 else "", 13, DIM, where="b", pad=6, weight="semibold")
        c.key_text(10, "|<", 16, FG, where="t", pad=4)
        c.key_text(12, "||" if st.get("status") == "Playing" else ">", 16, FG, where="t", pad=4)
        c.key_text(14, ">|", 16, FG, where="t", pad=4)
        return c.slice()

    def on_zoom_press(self, key: int):
        src = self.sources.get("media")
        cmd = {10: "prev", 12: "toggle", 14: "next"}.get(key)
        if cmd and src is not None and hasattr(src, "send"):
            src.send(cmd)
            return True  # stay in the zoom view
        return None
