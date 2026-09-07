"""The render loop: tiles on the board, zoom on press, toasts for alerts, ambient scenes when
idle, off when locked."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections import deque
from typing import Callable

from . import __version__
from .alerts import Alert, AlertWatcher, draw_badge, render_toast
from .ambient import SCENES, Scene, make_scene
from .config import ROOT
from .device import DeckBase
from .gfx import AMBER, new_key
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
        # An empty overlay value means "no overlay": config.local.toml merges into config.toml
        # key by key, so blanking one is the only way to switch off an overlay set in the tracked
        # file. It used to raise KeyError here, which also broke the next startup.
        if span == 1 and overlays.get(name):
            tile = OverlayTile(cfg, sources, make_tile(overlays[name], cfg, sources), tile)
        tile.slot, tile.span = i, span
        slots.extend([tile] * span)
        i += span
    return slots


class Request:
    """A command waiting for the loop thread; ``done`` is set once ``reply`` is filled in."""

    def __init__(self, cmd: str, args: list[str]):
        self.cmd = cmd
        self.args = args
        self.reply: dict = {"ok": False, "error": "not run"}
        self.done = threading.Event()


class App:
    def __init__(self, cfg: dict, deck: DeckBase, sources: dict | None = None, watcher: AlertWatcher | None = None,
                 config_loader: Callable[[], dict] | None = None):
        self.deck = deck
        self.config_loader = config_loader
        self._apply_settings(cfg)
        al = cfg.get("alerts", {})
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
        self._reset_fps_stats(time.time())
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
        self._slow_last_log = 0.0
        self._slow_suppressed = 0
        self.slow_total = 0
        self.last_ambient: dict | None = None
        self.commands: queue.Queue = queue.Queue()
        self.paused = False
        self.brightness_override: int | None = None
        self.started_at = time.time()
        self.pid = os.getpid()

    def _apply_settings(self, cfg: dict) -> None:
        """Read every tunable from ``cfg`` (at start and on ``reload``)."""
        self.cfg = cfg
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
        self.slow_step_s = float(dk.get("slow_step_ms", 100)) / 1000.0

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
        if not self.deck.opened:  # main() brings the faces up before it waits for the deck
            return "waiting"
        if self.locked:
            return "locked"
        if self.paused:
            return "paused"
        if self.toast is not None:
            return "toast"
        if self.zoom is not None:
            return "zoom"
        if self.scene is not None:
            return "ambient"
        return "board"

    @property
    def stopping(self) -> bool:
        return self._stop

    def tray_state(self) -> str:
        """on / off / badge: what the tray icon shows; grey until the deck opens, as the deck is dark.

        ``opened`` is cleared only by ``close()``, never by a transport error, so this reads grey
        before the first open and not during the in-place reconnect after a suspend."""
        if self.paused or self.locked or not self.deck.opened:
            return "off"
        return "badge" if self.badges else "on"

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
                time.sleep(self._sleep_s(time.monotonic() - t0))
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self.stop()

    def _sleep_s(self, elapsed: float, now: float | None = None) -> float:
        """Seconds to sleep after a tick: until the next tick, or sooner when an animation frame is due.

        Scenes and toasts pace themselves through ``scene_next`` / ``toast_next``. Snapping those to
        the 10 Hz tick grid quantized an 8 fps scene down to 5 fps, so the loop wakes for them.
        """
        if self.paused:
            return max(0.0, 1.0 - elapsed)
        due = self.tick_s - elapsed
        if not self.locked:
            now = time.time() if now is None else now
            if self.toast is not None:
                due = min(due, self.toast_next - now)
            elif self.scene is not None:
                due = min(due, self.scene_next - now)
        return max(0.0, due)

    def _fps_stats(self, now: float, flush_s: float, sent: int) -> None:
        """Once a minute in ambient mode, log the achieved frame rate and the flush cost (a hardware check)."""
        self._fps_flush += flush_s
        self._fps_flush_max = max(self._fps_flush_max, flush_s)
        self._fps_keys += sent
        elapsed = now - self._fps_since
        if elapsed >= 60 and self._fps_frames and self.scene is not None:
            self.last_ambient = {
                "scene": self.scene.name, "fps": self._fps_frames / elapsed, "target": min(self.ambient_fps, self.scene.fps),
                "flush_avg_ms": self._fps_flush / self._fps_frames * 1000, "flush_max_ms": self._fps_flush_max * 1000,
                "keys": self._fps_keys / self._fps_frames, "slow": self._fps_slow, "at": now,
            }
            a = self.last_ambient
            log.info("ambient %s: %.1f fps (target %g), flush avg %.0f ms max %.0f ms, %.1f keys per frame, %d slow ticks",
                     a["scene"], a["fps"], a["target"], a["flush_avg_ms"], a["flush_max_ms"], a["keys"], a["slow"])
            self._reset_fps_stats(now)

    def _reset_fps_stats(self, now: float) -> None:
        self._fps_frames = 0
        self._fps_flush = 0.0
        self._fps_flush_max = 0.0
        self._fps_keys = 0
        self._fps_slow = 0
        self._fps_since = now

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
        self._reset_fps_stats(now)
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

    # --- commands (ctl, tray, dashboard) -----------------------------------------------
    def submit(self, cmd: str, args: list[str], timeout: float = 3.0) -> dict:
        """Run a command on the loop thread from another thread and wait for the reply.

        The pipe, the dashboard and the tray are up before the deck is (main starts them first so a
        deck that never arrives is visible rather than silent), and until the loop turns nothing
        drains the queue. ``status`` is a read of attributes nothing is mutating yet, so it is
        answered here instead of after the timeout - it is how a client finds out what is wrong;
        so is ``quit``, which is the way out of a wait that would otherwise never end and must never
        be a dead menu item in the tray. Anything that would drive the board says why it cannot."""
        if not self.deck.opened:
            if cmd in ("status", "quit"):
                return self.command(cmd, list(args))
            return {"ok": False, "error": f"the deck is not open yet, so '{cmd}' has nothing to drive"}
        req = Request(cmd, list(args))
        self.commands.put(req)
        if not req.done.wait(timeout):
            return {"ok": False, "error": f"no answer from the render loop within {timeout:g} s"}
        return req.reply

    def _drain_commands(self, now: float) -> None:
        while True:
            try:
                req = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                req.reply = self.command(req.cmd, req.args, now)
            except Exception as exc:  # noqa: BLE001 - a bad command must not take the loop down
                log.exception("command %s failed", req.cmd)
                req.reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                req.done.set()

    def command(self, cmd: str, args: list[str], now: float | None = None) -> dict:
        """Execute one control command (on the loop thread; other threads go through ``submit``)."""
        now = time.time() if now is None else now
        if cmd == "status":
            return {"ok": True, **self.status(now)}
        if cmd == "wake":
            self.wake(now)
            return {"ok": True, "mode": self.mode}
        if cmd == "scene":
            name = args[0] if args else ""
            if name not in SCENES:
                return {"ok": False, "error": f"unknown scene '{name}' (known: {', '.join(SCENES)})"}
            if name in self.scene_names:
                self.scene_index = self.scene_names.index(name)
            self.zoom = None
            self.start_ambient(now, name)
            if self.scene is None or self.scene.name != name:
                return {"ok": False, "error": f"scene {name} failed to start"}
            return {"ok": True, "scene": name, "paused": self.paused}
        if cmd == "next":
            if not self.scene_names:
                return {"ok": False, "error": "no scenes configured"}
            if self.scene is not None:
                self.scene_index += 1
            self.zoom = None
            self.start_ambient(now)
            return {"ok": True, "scene": self.scene.name if self.scene else None, "paused": self.paused}
        if cmd == "pause":
            self.set_paused(True, now)
            return {"ok": True, "mode": self.mode}
        if cmd == "resume":
            self.set_paused(False, now)
            return {"ok": True, "mode": self.mode}
        if cmd == "brightness":
            value = (args[0] if args else "").strip().lower()
            if value == "auto":
                self.brightness_override = None
            else:
                try:
                    level = int(value)
                except ValueError:
                    level = -1
                if not 0 <= level <= 100:
                    return {"ok": False, "error": "brightness must be 0-100 or auto"}
                self.brightness_override = level
            self._apply_brightness(now, force=True)
            return {"ok": True, "brightness": self._current_brightness, "override": self.brightness_override}
        if cmd == "toast":
            kind, title = args[0], args[1]
            detail = args[2] if len(args) > 2 else ""
            self.toast_queue.append(Alert(kind=kind.upper(), title=title.upper(), subject="test", detail=detail, color=AMBER, style="alert"))
            return {"ok": True}
        if cmd == "reload":
            if self.config_loader is None:
                return {"ok": False, "error": "no config loader"}
            self.reload(self.config_loader())
            return {"ok": True, "tiles": [t.name for t in self.tiles], "scenes": list(self.scene_names)}
        if cmd == "quit":
            log.info("quit requested")
            self._stop = True
            return {"ok": True}
        return {"ok": False, "error": f"unknown command '{cmd}'"}

    def wake(self, now: float) -> None:
        """Back to the board from a scene or a zoom, as a key press would, without acting on a tile."""
        self.last_press = now
        if self.toast is not None:  # like a key press: dismissed, no badge
            self._end_toast(badge=False)
        if self.scene is not None:
            self.stop_ambient()
        if self.zoom is not None:
            self.zoom = None
            self._invalidate()

    def set_paused(self, paused: bool, now: float) -> None:
        """Paused: deck dark, one tick per second, nothing rendered; the sources keep polling."""
        if paused == self.paused:
            return
        self.paused = paused
        if paused:
            log.info("paused: deck off")
            self.stop_ambient()
            self.zoom = None
        else:
            log.info("resumed")
            self.last_press = now
            self._invalidate()
        self._apply_brightness(now, force=True)

    def reload(self, cfg: dict) -> None:
        """Take a fresh config: tunables, layout and scene list; the sources keep running.

        The slots are built first, into a local: ``build_slots`` raises on an unknown tile name, and
        a reload that reports an error must change nothing. Applying the settings first would leave
        brightness, the night window, idle_minutes and the scene list from a rejected file live
        behind the old layout."""
        slots = build_slots(cfg.get("layout", {}).get("keys", []), self.deck.key_count, cfg, self.sources)
        self._apply_settings(cfg)
        self.slots = slots
        self.badges = {}
        self.zoom = None
        self._invalidate()
        self._apply_brightness(time.time(), force=True)
        log.info("config reloaded: %s", ", ".join(t.name for t in self.tiles) or "no tiles")

    def status(self, now: float) -> dict:
        sources = {}
        for name, src in self.sources.items():
            last = float(getattr(src, "last_ok", 0.0) or 0.0)
            sources[name] = {"age_s": round(now - last, 1) if last else None, "error": getattr(src, "error", None),
                             "failures": int(getattr(src, "failures", 0) or 0)}
        deck_info = dict(getattr(self.deck, "info", None) or {}) or {"type": type(self.deck).__name__}
        deck_info["open"] = bool(self.deck.opened)  # false while main is still waiting for it
        return {
            "version": __version__, "pid": self.pid, "mode": self.mode, "scene": self.scene.name if self.scene else None,
            "paused": self.paused, "locked": self.locked, "brightness": self._current_brightness,
            "brightness_override": self.brightness_override, "uptime_s": round(now - self.started_at, 1),
            "idle_s": round(now - self.last_press, 1), "ticks": self.ticks, "slow_ticks": self.slow_total,
            "ambient": self.last_ambient, "badges": [a.tile for a in self.badges.values()],
            "toast": self.toast.title if self.toast else None, "tiles": [t.name for t in self.tiles],
            "scenes": list(self.scene_names), "deck": deck_info, "sources": sources,
        }

    # --- loop ------------------------------------------------------------------------
    def tick(self) -> None:
        now = time.time()
        self.ticks += 1
        marks: list[tuple[str, float]] = [("start", time.perf_counter())]
        fetched_before = self._fetch_marks()
        self._tick(now, marks)
        self._report_slow(now, marks, fetched_before)

    def _fetch_marks(self) -> dict[str, float]:
        return {name: getattr(src, "last_ok", 0.0) for name, src in self.sources.items()}

    def _tick(self, now: float, marks: list[tuple[str, float]]) -> None:
        """One pass of the loop; ``marks`` collects (step name, perf_counter) after each step."""
        self._drain_commands(now)
        marks.append(("commands", time.perf_counter()))
        while True:
            try:
                key = self.presses.get_nowait()
            except queue.Empty:
                break
            self._handle_press(key, now)
        marks.append(("presses", time.perf_counter()))
        if now - self._last_lock_check >= self.lock_poll_s:
            self._check_lock(now)
            marks.append(("lock", time.perf_counter()))
        if self.locked or self.paused:
            self.deck.flush(self.budget_s)
            marks.append(("flush", time.perf_counter()))
            return
        self._poll_alerts(now)
        marks.append(("alerts", time.perf_counter()))
        if self.toast is None and self.toast_queue:
            self._start_toast(self.toast_queue.popleft(), now)
        if self.toast is not None and now - self.toast_started >= self.toast_s:
            self._end_toast(badge=True)
        if self.toast is not None:
            if now >= self.toast_next:
                for i, img in enumerate(render_toast(self.toast, self.gap, now - self.toast_started)[: self.deck.key_count]):
                    self.deck.set_key_image(i, img)
                self.toast_next = now + self.anim_s
                marks.append(("toast", time.perf_counter()))
            self.deck.flush(self.budget_s)
            marks.append(("flush", time.perf_counter()))
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
                    self._fps_frames += 1
                marks.append(("scene", time.perf_counter()))
        elif self.zoom is not None:
            if now >= self.zoom_next:
                images = self.zoom.render_zoom(now) or []
                for i, img in enumerate(images[: self.deck.key_count]):
                    self.deck.set_key_image(i, img)
                self.zoom_next = now + self.zoom.zoom_refresh
                marks.append(("zoom", time.perf_counter()))
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
            marks.append(("tiles", time.perf_counter()))
        t_flush = time.perf_counter()
        sent = self.deck.flush(self.budget_s)
        marks.append(("flush", time.perf_counter()))
        if self.scene is not None:
            self._fps_stats(now, marks[-1][1] - t_flush, sent)
        if now - self._last_brightness_check >= 30:
            self._apply_brightness(now)
            marks.append(("brightness", time.perf_counter()))

    def _report_slow(self, now: float, marks: list[tuple[str, float]], fetched_before: dict[str, float]) -> None:
        """Name every tick step over ``deck.slow_step_ms`` (one warning per 10 s; the rest are counted).

        The sources that completed a fetch during the tick are listed too: a CPU-bound source thread
        can hold the GIL between the per-key writes of a flush.
        """
        slow = [f"{name} {(t1 - t0) * 1000:.0f} ms" for (_, t0), (name, t1) in zip(marks, marks[1:]) if t1 - t0 >= self.slow_step_s]
        if not slow:
            return
        self._fps_slow += 1
        self.slow_total += 1
        if now - self._slow_last_log < 10:
            self._slow_suppressed += 1
            return
        fetched = [n for n, t in self._fetch_marks().items() if t != fetched_before.get(n)]
        more = f" (+{self._slow_suppressed} more in the last 10 s)" if self._slow_suppressed else ""
        log.warning("slow tick in %s: %s; tick %.0f ms; sources fetched meanwhile: %s%s", self.mode, ", ".join(slow),
                    (marks[-1][1] - marks[0][1]) * 1000, ", ".join(fetched) or "none", more)
        self._slow_last_log = now
        self._slow_suppressed = 0

    def _is_night(self, now: float) -> bool:
        lt = time.localtime(now)
        m = lt.tm_hour * 60 + lt.tm_min
        if self.night_start > self.night_end:
            return m >= self.night_start or m < self.night_end
        return self.night_start <= m < self.night_end

    def _apply_brightness(self, now: float, force: bool = False) -> None:
        self._last_brightness_check = now
        if self.paused or self.locked:
            level = 0
        elif self.brightness_override is not None:
            level = self.brightness_override
        else:
            level = self.night_brightness if self._is_night(now) else self.brightness
        if force or level != self._current_brightness:
            self.deck.set_brightness(level)
            self._current_brightness = level
