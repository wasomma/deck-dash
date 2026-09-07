"""System tray icon (pystray): the app's face while it runs windowless under Task Scheduler.

pystray runs its own message loop on a thread (``run_detached``), so the render loop keeps the
main thread. Menu actions go through ``App.submit`` like every other client; a watcher thread
recolours the icon when the app's state changes. No pystray, no tray: the app runs without it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from .control import calibrate_script, open_window, restart_script, run_powershell
from .gfx import AMBER, DIM, GREEN

log = logging.getLogger(__name__)

STATE_COLORS = {"on": GREEN, "off": DIM, "badge": AMBER}
BRIGHTNESS_LEVELS = (100, 50, 30)


def icon_image(state: str, size: int = 64) -> Image.Image:
    """The 3x5 key grid as dots, coloured by state (on / off / badge)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = tuple(STATE_COLORS.get(state, DIM)) + (255,)
    cols, rows = 5, 3
    cell = size / cols
    r = cell * 0.36
    top = (size - rows * cell) / 2
    for row in range(rows):
        for col in range(cols):
            cx, cy = (col + 0.5) * cell, top + (row + 0.5) * cell
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    return img


class Tray:
    def __init__(self, app, root: Path, dashboard_url: str | None = None):
        self.app = app
        self.root = Path(root)
        self.dashboard_url = dashboard_url
        self.icon = None
        self.enabled = False
        self._pystray = None
        self._sig: tuple = ()

    # --- lifecycle ---------------------------------------------------------------------
    def start(self) -> bool:
        try:
            import pystray
        except ImportError as exc:
            log.warning("tray disabled (pip install pystray): %s", exc)
            return False
        self._pystray = pystray
        self._sig = self._signature()
        try:
            self.icon = pystray.Icon("deck-dash", icon_image(self._sig[0]), f"deck-dash: {self.app.mode}", self._menu())
            self.icon.run_detached()
        except Exception as exc:  # noqa: BLE001 - no tray is not a reason to stop rendering
            log.warning("tray disabled: %s", exc)
            self.icon = None
            return False
        threading.Thread(target=self._watch, name="tray-watch", daemon=True).start()
        self.enabled = True
        log.info("tray icon up")
        return True

    def stop(self) -> None:
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass

    def _signature(self) -> tuple:
        """Everything the icon, the tooltip and the menu are drawn from. pystray's win32 backend
        snapshots the menu into a native HMENU and only rebuilds it on ``update_menu()``, so a
        change made from ``ctl``, a key press, the lock or the dashboard has to be pushed here -
        otherwise "Pause" still reads Pause after ``ctl pause`` and clicking it resumes instead."""
        return (self.app.tray_state(), self.app.mode, self.app.paused,
                self.app.brightness_override, tuple(self.app.scene_names))

    def _sync(self) -> bool:
        """One pass: push anything that changed to the icon. True when something was pushed."""
        sig = self._signature()
        if sig == self._sig:
            return False
        state, mode, _, _, scenes = sig
        try:
            if state != self._sig[0]:
                self.icon.icon = icon_image(state)
            if mode != self._sig[1]:
                self.icon.title = f"deck-dash: {mode}"
            if scenes != self._sig[4]:
                self.icon.menu = self._menu()  # reload can change the scene list
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            log.debug("tray update failed: %s", exc)
        self._sig = sig
        return True

    def _watch(self) -> None:
        while self.icon is not None and not self.app.stopping:
            self._sync()
            time.sleep(1.0)

    # --- menu --------------------------------------------------------------------------
    def _menu(self):
        Item, Menu = self._pystray.MenuItem, self._pystray.Menu
        scenes = [Item(name, self._cmd("scene", name)) for name in self.app.scene_names]
        scenes += [Menu.SEPARATOR, Item("Next", self._cmd("next"))]
        levels = [Item("Auto", self._cmd("brightness", "auto"), checked=lambda item: self.app.brightness_override is None, radio=True)]
        for n in BRIGHTNESS_LEVELS:
            levels.append(Item(f"{n}%", self._cmd("brightness", str(n)), checked=self._level_checked(n), radio=True))
        return Menu(
            Item("Open dashboard", self._open_dashboard, default=True),
            Item("Wake board", self._cmd("wake")),
            Item("Scene", Menu(*scenes)),
            Item(lambda item: "Resume" if self.app.paused else "Pause", self._toggle_pause),
            Item("Brightness", Menu(*levels)),
            Menu.SEPARATOR,
            Item("Open log", self._open_log),
            Item("Edit config", self._edit_config),
            Item("Recalibrate gap", self._recalibrate),
            Item("Restart", self._restart),
            Menu.SEPARATOR,
            Item("Quit", self._cmd("quit")),
        )

    def _level_checked(self, n: int):
        return lambda item: self.app.brightness_override == n

    def _cmd(self, cmd: str, *args: str):
        def action() -> None:
            reply = self.app.submit(cmd, list(args))
            if not reply.get("ok"):
                log.warning("tray %s %s: %s", cmd, " ".join(args), reply.get("error"))
            self._refresh()
        return action

    def _refresh(self) -> None:
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:  # noqa: BLE001
                pass

    def _toggle_pause(self) -> None:
        self._cmd("resume" if self.app.paused else "pause")()

    def _open_dashboard(self) -> None:
        if self.dashboard_url:
            open_window(self.dashboard_url)
        else:
            log.info("no dashboard yet (Phase 7b)")

    def _open_log(self) -> None:
        _startfile(self.root / "logs" / "deckdash.log")

    def _edit_config(self) -> None:
        path = config.LOCAL_CONFIG
        if not path.exists():
            config.write_local({}, path)
        _startfile(path)

    def _recalibrate(self) -> None:
        run_powershell(calibrate_script(self.root), console=True)

    def _restart(self) -> None:
        run_powershell(restart_script(self.root))


def _startfile(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except (AttributeError, OSError) as exc:
        log.warning("cannot open %s: %s", path, exc)
