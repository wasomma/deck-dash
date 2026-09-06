"""The render loop: tiles on the board, zoom on press, toasts for alerts, ambient scenes when
idle, off when locked."""

from __future__ import annotations

import logging
import queue
import time
from collections import deque

from .alerts import Alert, AlertWatcher, draw_badge, render_toast
from .ambient import SCENES, Scene, make_scene
from .config import ROOT
from .device import DeckBase
from .gfx import new_key
from .session import session_locked
from .sources import make_sources
from .tiles import OverlayTile, Tile, make_tile

log = logging.getLogger(__name__)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def build_slots(names: list[str], key_count: int, cfg: dict, sources: dict) -> list[Tile | None]:
    """One tile per key; a wide tile listed on consecutive keys gets a single instance spanning
    them; ``[layout] overlays`` wraps a base tile with one that takes the key over when active."""
    names = (list(names) + [""] * key_count)[:key_count]
    overlays = dict(cfg.get("layout", {}).get("overlays", {}))
    slots: list[Tile | None] = []
    i = 0
    while i < key_count:
        name = names[i]
        if not name:
            slots.append(None)
            i += 1
            continue
        tile = make_tile(name, cfg, sources)
        span = 1
        while span < tile.width and i + span < key_count and names[i + span] == name:
            span += 1
        if span == 1 and name in overlays:
            tile = OverlayTile(cfg, sources, make_tile(overlays[name], cfg, sources), tile)
        tile.slot, tile.span = i, span
        slots.extend([tile] * span)
        i += span
    return slots


class App:
    def __init__(self, cfg: dict, deck: DeckBase, sources: dict | None = None, watcher: AlertWatcher | None = None):
        self.cfg = cfg
        self.deck = deck
        dk = cfg.get("deck", {})
        am = cfg.get("ambient", {})
        al = cfg.get("alerts", {})
        self.tick_s = 1.0 / float(dk.get("tick_hz", 10))
        self.budget_s = float(dk.get("flush_budget_ms", 60)) / 1000.0
        self.anim_s = 1.0 / float(dk.get("anim_fps", 8))
        self.gap = int(dk.get("gap_px", 24))
        self.zoom_s = float(dk.get("zoom_seconds", 10))
        self.brightness = int(dk.get("brightness", 80))
        self.night_brightness = int(dk.get("night_brightness", 30))
        self.night_start = _minutes(dk.get("night_start", "22:00"))
        self.night_end = _minutes(dk.get("night_end", "07:00"))
        self.idle_s = float(dk.get("idle_minutes", 10)) * 60
        self.lock_poll_s = float(dk.get("lock_poll_seconds", 5))
        self.lock_check = session_locked if bool(dk.get("off_on_lock", True)) else (lambda: False)
        self.scene_names = [s for s in am.get("scenes", list(SCENES)) if s in SCENES]
        self.scene_s = float(am.get("scene_minutes", 5)) * 60
        self.ambient_fps = float(am.get("fps", 8))
        self.toast_s = float(al.get("toast_seconds", 5))
        self.sources = sources if sources is not None else make_sources(cfg)
        if watcher is not None:
            self.watcher: AlertWatcher | None = watcher
        elif bool(al.get("enabled", True)):
            self.watcher = AlertWatcher(cfg, self.sources, ROOT / "state" / "alerts.json")
        else:
            self.watcher = None
        self.slots = build_slots(cfg.get("layout", {}).get("keys", []), deck.key_count, cfg, self.sources)
        self.presses: queue.Queue = queue.Queue()
        self.zoom: Tile | None = None
        self.zoom_until = 0.0
        self.zoom_next = 0.0
        self.scene: Scene | None = None
        self.scene_index = 0
        self.scene_started = 0.0
        self.scene_next = 0.0
        self.forced_scene: str | None = None
        self.toast: Alert | None = None
        self.toast_started = 0.0
        self.toast_next = 0.0
        self.toast_queue: deque = deque(maxlen=5)
        self.badges: dict[int, Alert] = {}
        self._last_alert_check = 0.0
        self.locked = False
        self._lock_hits = 0
        self._last_lock_check = 0.0
        self.last_press = time.time()
        self._current_brightness: int | None = None
        self._last_brightness_check = 0.0
        self.ticks = 0
        self._stop = False

    @property
    def tiles(self) -> list[Tile]:
        """Distinct tiles on the board, in key order."""
        seen: list[Tile] = []
        for t in self.slots:
            if t is not None and t not in seen:
                seen.append(t)
        return seen

    @property
    def mode(self) -> str:
        if self.locked:
            return "locked"
        if self.toast is not None:
            return "toast"
        if self.zoom is not None:
            return "zoom"
        if self.scene is not None:
            return "ambient"
        return "board"

    # --- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        for s in self.sources.values():
            s.start()
        if not self.deck.opened:  # main() may have opened it already (retry loop)
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
        log.info("key %d pressed (%s)", key, self.mode)
        if self.toast is not None:  # acknowledged: no badge
            self._end_toast(badge=False)
            return
        if self.scene is not None:  # any key wakes the board; nothing else happens
            self.stop_ambient()
            return
        if self.zoom is not None:
            stay = None
            try:
                stay = self.zoom.on_zoom_press(key)
            except Exception:  # noqa: BLE001
                log.exception("zoom press handler failed")
            if stay:
                self.zoom_until = now + self.zoom_s
                self.zoom_next = 0.0
                return
            self.zoom = None
            self._invalidate()
            return
        tile = self.slots[key] if 0 <= key < len(self.slots) else None
        if tile is None:
            return
        self.badges.pop(tile.slot, None)
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

    # --- ambient ---------------------------------------------------------------------
    def start_ambient(self, now: float, name: str | None = None) -> None:
        if name is None:
            if not self.scene_names:
                return
            name = self.scene_names[self.scene_index % len(self.scene_names)]
        try:
            self.scene = make_scene(name, self.cfg, self.sources)
        except Exception:  # noqa: BLE001 - a broken scene must not take the loop down
            log.exception("scene %s failed to start", name)
            self.scene_index += 1
            return
        self.scene_started = now
        self.scene_next = 0.0
        log.info("ambient: %s", name)

    def stop_ambient(self) -> None:
        if self.scene is not None:
            log.info("ambient off")
            self.scene = None
            self._invalidate()

    # --- alerts ----------------------------------------------------------------------
    def _badge_slot(self, tile_name: str) -> int | None:
        for tile in self.tiles:
            if tile.matches(tile_name):
                return tile.slot
        return None

    def _start_toast(self, alert: Alert, now: float) -> None:
        log.info("toast: %s %s %s (%s)", alert.kind, alert.title, alert.subject, alert.detail)
        self.stop_ambient()
        self.zoom = None
        self.toast = alert
        self.toast_started = now
        self.toast_next = 0.0

    def _end_toast(self, badge: bool) -> None:
        alert = self.toast
        self.toast = None
        if alert is not None:
            slot = self._badge_slot(alert.tile) if alert.tile else None
            if slot is not None:
                if badge and alert.style != "good":
                    self.badges[slot] = alert
                elif alert.style == "good":
                    self.badges.pop(slot, None)
        self._invalidate()

    def _poll_alerts(self, now: float) -> None:
        if self.watcher is None or now - self._last_alert_check < 1.0:
            return
        self._last_alert_check = now
        try:
            for alert in self.watcher.check(now):
                self.toast_queue.append(alert)
        except Exception:  # noqa: BLE001
            log.exception("alert check failed")

    # --- lock ------------------------------------------------------------------------
    def _check_lock(self, now: float) -> None:
        self._last_lock_check = now
        try:
            hit = bool(self.lock_check())
        except Exception:  # noqa: BLE001
            hit = False
        self._lock_hits = self._lock_hits + 1 if hit else 0
        if not self.locked and self._lock_hits >= 2:  # two polls in a row: not just a UAC prompt
            self.locked = True
            log.info("session locked: deck off")
            self.deck.set_brightness(0)
            self._current_brightness = 0
        elif self.locked and not hit:
            self.locked = False
            log.info("session unlocked: deck on")
            self.last_press = now
            self.stop_ambient()
            self._invalidate()
            self._apply_brightness(now, force=True)

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
        if now - self._last_lock_check >= self.lock_poll_s:
            self._check_lock(now)
        if self.locked:
            self.deck.flush(self.budget_s)
            return
        self._poll_alerts(now)
        if self.toast is None and self.toast_queue:
            self._start_toast(self.toast_queue.popleft(), now)
        if self.toast is not None and now - self.toast_started >= self.toast_s:
            self._end_toast(badge=True)
        if self.toast is not None:
            if now >= self.toast_next:
                for i, img in enumerate(render_toast(self.toast, self.gap, now - self.toast_started)[: self.deck.key_count]):
                    self.deck.set_key_image(i, img)
                self.toast_next = now + self.anim_s
            self.deck.flush(self.budget_s)
            return
        if self.zoom is not None and now >= self.zoom_until:
            self.zoom = None
            self._invalidate()
        if self.zoom is None and self.scene is None and self.idle_s > 0 and self.scene_names and now - self.last_press >= self.idle_s:
            self.start_ambient(now, self.forced_scene)
        if self.scene is not None:
            if self.forced_scene is None and self.scene_s > 0 and now - self.scene_started >= self.scene_s:
                self.scene_index += 1
                self.start_ambient(now)
            if self.scene is not None and now >= self.scene_next:
                try:
                    images = self.scene.frame(now)
                except Exception:  # noqa: BLE001
                    log.exception("scene %s failed to render", self.scene.name)
                    self.scene_index += 1
                    self.start_ambient(now)
                    images = []
                for i, img in enumerate(images[: self.deck.key_count]):
                    self.deck.set_key_image(i, img)
                if self.scene is not None:
                    self.scene_next = now + 1.0 / max(0.5, min(self.ambient_fps, self.scene.fps))
        elif self.zoom is not None:
            if now >= self.zoom_next:
                images = self.zoom.render_zoom(now) or []
                for i, img in enumerate(images[: self.deck.key_count]):
                    self.deck.set_key_image(i, img)
                self.zoom_next = now + self.zoom.zoom_refresh
        else:
            for tile in self.tiles:
                if now < tile.next_due:
                    continue
                try:
                    if tile.span > 1:
                        images = tile.render_span(now)[: tile.span]
                    else:
                        images = [tile.render(now)]
                    badge = self.badges.get(tile.slot)
                    if badge is not None:
                        images[0] = draw_badge(images[0], badge.color)
                    for k, img in enumerate(images):
                        self.deck.set_key_image(tile.slot + k, img)
                except Exception:  # noqa: BLE001 - one broken tile must not stop the board
                    log.exception("tile %s failed to render", tile.name)
                    self.deck.set_key_image(tile.slot, tile.placeholder(tile.name, "render error"))
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
