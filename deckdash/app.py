"""The render loop: tiles on the board, zoom on press, brightness schedule."""

from __future__ import annotations

import logging
import queue
import time

from .device import DeckBase
from .gfx import new_key
from .sources import make_sources
from .tiles import Tile, make_tile

log = logging.getLogger(__name__)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class App:
    def __init__(self, cfg: dict, deck: DeckBase, sources: dict | None = None):
        self.cfg = cfg
        self.deck = deck
        dk = cfg.get("deck", {})
        self.tick_s = 1.0 / float(dk.get("tick_hz", 10))
        self.budget_s = float(dk.get("flush_budget_ms", 60)) / 1000.0
        self.zoom_s = float(dk.get("zoom_seconds", 10))
        self.brightness = int(dk.get("brightness", 80))
        self.night_brightness = int(dk.get("night_brightness", 30))
        self.night_start = _minutes(dk.get("night_start", "22:00"))
        self.night_end = _minutes(dk.get("night_end", "07:00"))
        self.sources = sources if sources is not None else make_sources(cfg)
        names = list(cfg.get("layout", {}).get("keys", []))
        names += [""] * (deck.key_count - len(names))
        self.slots: list[Tile | None] = [make_tile(n, cfg, self.sources) if n else None for n in names[: deck.key_count]]
        self.presses: queue.Queue = queue.Queue()
        self.zoom: Tile | None = None
        self.zoom_until = 0.0
        self.zoom_next = 0.0
        self.last_press = time.time()
        self._current_brightness: int | None = None
        self._last_brightness_check = 0.0
        self.ticks = 0
        self._stop = False

    # --- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        for s in self.sources.values():
            s.start()
        self.deck.open()
        self.deck.on_press(self._on_press)
        self._invalidate()
        self._apply_brightness(time.time(), force=True)

    def stop(self) -> None:
        self._stop = True
        for s in self.sources.values():
            s.stop()
        self.deck.close()

    def run(self, max_seconds: float | None = None) -> None:
        self.start()
        deadline = time.monotonic() + max_seconds if max_seconds else None
        try:
            while not self._stop:
                t0 = time.monotonic()
                self.tick()
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(max(0.0, self.tick_s - (time.monotonic() - t0)))
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self.stop()

    # --- input -----------------------------------------------------------------------
    def _on_press(self, key: int, down: bool) -> None:
        if down:
            self.presses.put(key)

    def _handle_press(self, key: int, now: float) -> None:
        self.last_press = now
        if self.zoom is not None:
            self.zoom = None
            self._invalidate()
            return
        tile = self.slots[key] if 0 <= key < len(self.slots) else None
        if tile is None:
            return
        tile.on_press()
        if tile.zoomable:
            self.zoom = tile
            self.zoom_until = now + self.zoom_s
            self.zoom_next = 0.0

    def _invalidate(self) -> None:
        blank = new_key()
        for i, tile in enumerate(self.slots):
            if tile is None:
                self.deck.set_key_image(i, blank)
            else:
                tile.next_due = 0.0

    # --- loop ------------------------------------------------------------------------
    def tick(self) -> None:
        now = time.time()
        self.ticks += 1
        while True:
            try:
                key = self.presses.get_nowait()
            except queue.Empty:
                break
            self._handle_press(key, now)
        if self.zoom is not None and now >= self.zoom_until:
            self.zoom = None
            self._invalidate()
        if self.zoom is not None:
            if now >= self.zoom_next:
                images = self.zoom.render_zoom(now) or []
                for i, img in enumerate(images[: self.deck.key_count]):
                    self.deck.set_key_image(i, img)
                self.zoom_next = now + self.zoom.zoom_refresh
        else:
            for i, tile in enumerate(self.slots):
                if tile is not None and now >= tile.next_due:
                    try:
                        self.deck.set_key_image(i, tile.render(now))
                    except Exception:  # noqa: BLE001 - one broken tile must not stop the board
                        log.exception("tile %s failed to render", tile.name)
                        self.deck.set_key_image(i, tile.placeholder(tile.name, "render error"))
                    tile.next_due = now + tile.refresh
        self.deck.flush(self.budget_s)
        if now - self._last_brightness_check >= 30:
            self._apply_brightness(now)

    def _is_night(self, now: float) -> bool:
        lt = time.localtime(now)
        m = lt.tm_hour * 60 + lt.tm_min
        if self.night_start > self.night_end:
            return m >= self.night_start or m < self.night_end
        return self.night_start <= m < self.night_end

    def _apply_brightness(self, now: float, force: bool = False) -> None:
        self._last_brightness_check = now
        level = self.night_brightness if self._is_night(now) else self.brightness
        if force or level != self._current_brightness:
            self.deck.set_brightness(level)
            self._current_brightness = level
