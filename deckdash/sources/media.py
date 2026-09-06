"""Now playing, from the Windows media session API through ``tools/media_watch.ps1``.

The PowerShell helper runs for the life of the app and prints one JSON line per second;
this source keeps the latest one and decodes the album art when it changes. Playback
commands go back through a small command file the helper polls.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
HELPER = ROOT / "tools" / "media_watch.ps1"
STATE_DIR = ROOT / "state"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_line(line: str, prev: dict, now: float) -> dict:
    """Merge one helper line into the running state (art survives lines that omit it)."""
    j = json.loads(line)
    st = dict(prev)
    status = j.get("status", "None")
    st["status"] = status
    st["error"] = j.get("error")
    st["sampled"] = now
    if status in ("Playing", "Paused", "Stopped", "Changing", "Opened", "Closed"):
        st.update({
            "title": j.get("title") or "",
            "artist": j.get("artist") or "",
            "album": j.get("album") or "",
            "app": j.get("app") or "",
            "pos": float(j.get("pos") or 0.0),
            "dur": float(j.get("dur") or 0.0),
            "updated": float(j.get("updated") or 0.0),
            "art_key": j.get("art_key") or "",
        })
        if j.get("art"):
            try:
                st["art"] = Image.open(io.BytesIO(base64.b64decode(j["art"]))).convert("RGB")
                st["art_of"] = st["art_key"]
            except Exception as exc:  # noqa: BLE001 - odd thumbnail formats are not fatal
                log.debug("art decode failed: %s", exc)
        if st.get("art_of") != st.get("art_key"):
            st["art"] = None
        if status == "Playing":
            st["last_playing"] = now
    else:
        st["title"] = ""
        st["art"] = None
    return st


def is_active(st: dict, now: float, paused_grace_s: float = 120.0, ignore_apps: list[str] | None = None) -> bool:
    """Show the tile while something plays, and for a while after it was paused.

    ``ignore_apps``: case-insensitive substrings of the source app id to skip (e.g. a browser
    playing a stream), because Windows reports every media session, not just music players.
    """
    if not st:
        return False
    app = str(st.get("app", "")).lower()
    if any(s.lower() in app for s in (ignore_apps or []) if s):
        return False
    if st.get("status") == "Playing":
        return True
    if st.get("status") == "Paused" and st.get("title"):
        return now - float(st.get("last_playing", 0)) < paused_grace_s
    return False


class MediaSource:
    """Same surface as ``Poller`` (start/stop/state/error) but fed by the helper process."""

    def __init__(self, cfg: dict):
        m = cfg.get("nowplaying", {})
        self.enabled = bool(m.get("enabled", True)) and sys.platform == "win32"
        self.interval = float(m.get("poll_seconds", 1.0))
        self._lock = threading.Lock()
        self._state: dict = {}
        self.error: str | None = None
        self.last_ok = 0.0
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def state(self) -> dict:
        with self._lock:
            return copy.copy(self._state)

    def start(self) -> None:
        if not self.enabled or not HELPER.exists():
            self.error = "disabled" if not self.enabled else "helper missing"
            return
        self._thread = threading.Thread(target=self._run, name="media-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()

    def send(self, command: str) -> None:
        """toggle / next / prev, picked up by the helper within a second."""
        if command not in ("toggle", "next", "prev"):
            return
        STATE_DIR.mkdir(exist_ok=True)
        tmp = STATE_DIR / "media-cmd.tmp"
        tmp.write_text(command, encoding="ascii")
        os.replace(tmp, STATE_DIR / "media-cmd.txt")

    # --- helper process --------------------------------------------------------------
    def _kill(self) -> None:
        p = self._proc
        self._proc = None
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _run(self) -> None:
        backoff = 5.0
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(HELPER), "-Interval", str(self.interval)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW, cwd=str(ROOT),
                )
                for line in self._proc.stdout:  # type: ignore[union-attr]
                    if self._stop.is_set():
                        break
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        st = parse_line(line, self._state, time.time())
                    except (ValueError, TypeError) as exc:
                        self.error = f"bad line: {exc}"[:80]
                        continue
                    with self._lock:
                        self._state = st
                    if st.get("status") == "Error":
                        self.error = (st.get("error") or "helper error")[:120]
                    else:
                        self.error = None
                        self.last_ok = time.time()
                        backoff = 5.0
            except OSError as exc:
                self.error = f"helper: {exc}"[:120]
            finally:
                self._kill()
            if self._stop.is_set():
                break
            log.warning("media helper exited; restarting in %.0f s", backoff)
            with self._lock:
                self._state = {}
            self._stop.wait(backoff)
            backoff = min(60.0, backoff * 2)
