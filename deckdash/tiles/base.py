from __future__ import annotations

from PIL import Image, ImageDraw

from ..gfx import DIM, new_key, text


class Tile:
    """One key on the board. ``render`` returns a 72x72 RGB image; ``render_zoom`` 15 of them."""

    name = "tile"
    refresh = 1.0        # seconds between renders on the board
    zoom_refresh = 1.0   # seconds between renders while zoomed
    zoomable = False

    def __init__(self, cfg: dict, sources: dict):
        self.cfg = cfg
        self.sources = sources
        self.gap = int(cfg.get("deck", {}).get("gap_px", 24))
        self.next_due = 0.0

    def render(self, now: float) -> Image.Image:
        raise NotImplementedError

    def render_zoom(self, now: float) -> list[Image.Image] | None:
        return None

    def on_press(self) -> None:
        pass

    def placeholder(self, title: str, sub: str | None = None) -> Image.Image:
        img = new_key()
        d = ImageDraw.Draw(img)
        text(d, (36, 30), title, 12, DIM)
        if sub:
            sub = sub if len(sub) <= 12 else sub[:11] + "…"
            text(d, (36, 46), sub, 8, DIM, weight="regular")
        return img
