from __future__ import annotations

from PIL import Image, ImageDraw

from ..gfx import DIM, new_key, text


class Tile:
    """One key on the board. ``render`` returns a 72x72 RGB image; ``render_zoom`` 15 of them.

    A tile with ``width > 1`` owns that many consecutive keys and renders them together
    through ``render_span``; the layout lists its name once per key it occupies.
    """

    name = "tile"
    refresh = 1.0        # seconds between renders on the board
    zoom_refresh = 1.0   # seconds between renders while zoomed
    zoomable = False
    width = 1

    def __init__(self, cfg: dict, sources: dict):
        self.cfg = cfg
        self.sources = sources
        self.gap = int(cfg.get("deck", {}).get("gap_px", 24))
        self.next_due = 0.0
        self.slot = 0        # first key index on the board (set by the app)
        self.span = 1        # keys actually granted on the board (set by the app)

    def render(self, now: float) -> Image.Image:
        raise NotImplementedError

    def render_span(self, now: float) -> list[Image.Image]:
        """Images for every key of a wide tile, left to right."""
        return [self.render(now)]

    def render_zoom(self, now: float) -> list[Image.Image] | None:
        return None

    def on_press(self) -> None:
        pass

    def on_zoom_press(self, key: int):
        """A key was pressed while this tile's zoom view was showing. Return True to stay zoomed."""
        return None

    def active(self, now: float) -> bool:
        """For tiles that overlay another key: True while there is something to show."""
        return True

    def matches(self, name: str) -> bool:
        return name == self.name

    def placeholder(self, title: str, sub: str | None = None) -> Image.Image:
        img = new_key()
        d = ImageDraw.Draw(img)
        text(d, (36, 30), title, 12, DIM)
        if sub:
            sub = sub if len(sub) <= 12 else sub[:11] + "…"
            text(d, (36, 46), sub, 8, DIM, weight="regular")
        return img
