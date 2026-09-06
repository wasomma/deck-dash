"""Deck abstraction: the real Stream Deck over HID, or a simulator that writes PNGs.

Both keep a per-key hash of the last image sent and only transmit keys whose pixels
changed. On the gen-1 deck every key is ~15.5 KB of raw BMP over HID, so the byte budget
per tick is the thing that keeps the loop responsive.
"""

from __future__ import annotations

import logging
import os
import time
import zlib
from pathlib import Path
from typing import Callable

from PIL import Image

from .canvas import Canvas

log = logging.getLogger(__name__)

PressCallback = Callable[[int, bool], None]

_dll_dir_handles: list = []


def add_hidapi_dir(path: str | None) -> None:
    """Make hidapi.dll loadable: Python 3.8+ ignores PATH for ctypes, so register the folder."""
    if not path:
        return
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        log.warning("hidapi_dir does not exist: %s", path)
        return
    os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        _dll_dir_handles.append(os.add_dll_directory(path))


class DeckBase:
    key_count = 15
    cols = 5
    rows = 3

    def __init__(self) -> None:
        self._pending: list[Image.Image | None] = [None] * self.key_count
        self._hash: list[int | None] = [None] * self.key_count
        self._cursor = 0
        self._press_cb: PressCallback | None = None
        self.sent = 0  # key images actually transmitted (for tests and stats)
        self.opened = False

    # --- lifecycle -------------------------------------------------------------------
    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def set_brightness(self, percent: int) -> None:
        raise NotImplementedError

    def on_press(self, cb: PressCallback) -> None:
        self._press_cb = cb

    # --- images ----------------------------------------------------------------------
    def set_key_image(self, idx: int, img: Image.Image) -> None:
        """Queue an image for a key; it is sent on the next flush if it differs from what is shown."""
        self._pending[idx] = img

    def invalidate(self) -> None:
        """Forget what is on the deck so every pending image is resent (after a reconnect)."""
        self._hash = [None] * self.key_count

    def flush(self, budget_s: float) -> int:
        """Send changed keys, round-robin, until the time budget is spent. Returns keys sent."""
        start = time.perf_counter()
        n = self.key_count
        sent = 0
        stopped_at = None
        for k in range(n):
            i = (self._cursor + k) % n
            img = self._pending[i]
            if img is None:
                continue
            h = zlib.crc32(img.tobytes())
            if h == self._hash[i]:
                self._pending[i] = None
                continue
            if self._send(i, img):
                self._hash[i] = h
                self._pending[i] = None
                sent += 1
            if time.perf_counter() - start > budget_s:
                stopped_at = i
                break
        self._cursor = 0 if stopped_at is None else (stopped_at + 1) % n
        self.sent += sent
        self._after_flush()
        return sent

    def _send(self, idx: int, img: Image.Image) -> bool:
        raise NotImplementedError

    def _after_flush(self) -> None:
        pass


class RealDeck(DeckBase):
    def __init__(self, hidapi_dir: str | None = None, brightness: int = 80):
        super().__init__()
        self.hidapi_dir = hidapi_dir
        self.brightness = brightness
        self._deck = None
        self._to_native = None
        self._retry_at = 0.0
        self.info: dict = {}

    def open(self) -> None:
        add_hidapi_dir(self.hidapi_dir)
        from StreamDeck.DeviceManager import DeviceManager
        from StreamDeck.ImageHelpers import PILHelper

        decks = DeviceManager().enumerate()
        if not decks:
            raise RuntimeError("no Stream Deck found (is it plugged in, and is hidapi.dll in hidapi_dir?)")
        deck = decks[0]
        deck.open()
        deck.reset()
        self._deck = deck
        self._to_native = getattr(PILHelper, "to_native_key_format", None) or PILHelper.to_native_format
        self.key_count = deck.key_count()
        self.info = {
            "type": deck.deck_type(),
            "serial": deck.get_serial_number(),
            "firmware": deck.get_firmware_version(),
            "keys": self.key_count,
            "format": deck.key_image_format(),
        }
        log.info("deck open: %s", self.info)
        self._pending = [None] * self.key_count
        self.invalidate()
        deck.set_brightness(self.brightness)
        deck.set_key_callback(self._callback)
        self.opened = True

    def close(self) -> None:
        self.opened = False
        if self._deck is None:
            return
        try:
            self._deck.reset()
            self._deck.close()
        except Exception as exc:  # noqa: BLE001 - best effort on shutdown
            log.warning("close failed: %s", exc)
        self._deck = None

    def set_brightness(self, percent: int) -> None:
        self.brightness = percent
        if self._deck is not None:
            try:
                self._deck.set_brightness(percent)
            except Exception as exc:  # noqa: BLE001
                log.warning("set_brightness failed: %s", exc)

    def _callback(self, _deck, key: int, state: bool) -> None:
        if self._press_cb is not None:
            self._press_cb(key, state)

    def _send(self, idx: int, img: Image.Image) -> bool:
        if self._deck is None:
            self._maybe_reconnect()
            return False
        try:
            self._deck.set_key_image(idx, self._to_native(self._deck, img))
            return True
        except Exception as exc:  # noqa: BLE001 - transport errors when the deck is unplugged
            log.error("deck write failed, will reconnect: %s", exc)
            self._deck = None
            self._retry_at = time.monotonic() + 5
            return False

    def _maybe_reconnect(self) -> None:
        if time.monotonic() < self._retry_at:
            return
        self._retry_at = time.monotonic() + 5
        try:
            self.open()
        except Exception as exc:  # noqa: BLE001
            log.warning("reconnect failed: %s", exc)


class SimDeck(DeckBase):
    """Writes the deck as ``<out_dir>/canvas.png``; ``<out_dir>/press.txt`` injects a key press."""

    def __init__(self, gap: int = 24, out_dir: str | Path = "sim", interval: float = 1.0, scale: int = 2):
        super().__init__()
        self.gap = gap
        self.out_dir = Path(out_dir)
        self.interval = interval
        self.scale = scale
        self._shown: list[Image.Image | None] = [None] * self.key_count
        self._last_write = 0.0
        self.brightness = 80
        self.frames = 0

    def open(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.opened = True

    def close(self) -> None:
        self.opened = False
        self.write()

    def set_brightness(self, percent: int) -> None:
        self.brightness = percent

    def _send(self, idx: int, img: Image.Image) -> bool:
        self._shown[idx] = img.copy()
        return True

    def _after_flush(self) -> None:
        self._poll_press()
        if time.monotonic() - self._last_write >= self.interval:
            self.write()

    def write(self) -> Path:
        self._last_write = time.monotonic()
        board = Canvas.compose(self._shown, self.gap, scale=self.scale)
        out = self.out_dir / "canvas.png"
        tmp = out.with_suffix(".tmp.png")
        board.save(tmp)
        os.replace(tmp, out)
        self.frames += 1
        return out

    def _poll_press(self) -> None:
        p = self.out_dir / "press.txt"
        if not p.exists():
            return
        try:
            key = int(p.read_text().strip())
        except ValueError:
            key = -1
        try:
            p.unlink()
        except OSError:
            pass
        if 0 <= key < self.key_count and self._press_cb is not None:
            self._press_cb(key, True)
            self._press_cb(key, False)
