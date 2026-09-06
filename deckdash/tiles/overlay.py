"""A key shared by two tiles: the top one shows while it has something to say, the base otherwise."""

from __future__ import annotations

from .base import Tile


class OverlayTile(Tile):
    def __init__(self, cfg: dict, sources: dict, top: Tile, base: Tile):
        super().__init__(cfg, sources)
        self.top = top
        self.base = base
        self.current = base
        self.name = f"{top.name}|{base.name}"

    def matches(self, name: str) -> bool:
        return name in (self.top.name, self.base.name)

    def _pick(self, now: float) -> Tile:
        try:
            self.current = self.top if self.top.active(now) else self.base
        except Exception:  # noqa: BLE001 - a broken overlay falls back to the base tile
            self.current = self.base
        return self.current

    @property
    def zoomable(self) -> bool:  # type: ignore[override]
        return self.current.zoomable

    @property
    def zoom_refresh(self) -> float:  # type: ignore[override]
        return self.current.zoom_refresh

    def render(self, now):
        tile = self._pick(now)
        img = tile.render(now)
        self.refresh = tile.refresh
        return img

    def render_zoom(self, now):
        return self.current.render_zoom(now)

    def on_press(self) -> None:
        self.current.on_press()

    def on_zoom_press(self, key: int):
        return self.current.on_zoom_press(key)
