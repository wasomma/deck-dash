from __future__ import annotations

import copy
import logging
import threading
import time

log = logging.getLogger(__name__)


class Poller(threading.Thread):
    """Calls ``fetch()`` every ``interval`` seconds on a daemon thread; keeps the last good state."""

    def __init__(self, name: str, interval: float):
        super().__init__(name=f"poll-{name}", daemon=True)
        self.interval = interval
        self._lock = threading.Lock()
        self._state: dict = {}
        self._stop = threading.Event()
        self.error: str | None = None
        self.last_ok: float = 0.0
        self.failures = 0

    @property
    def state(self) -> dict:
        with self._lock:
            return copy.copy(self._state)

    def fetch(self) -> dict:
        raise NotImplementedError

    def run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                new = self.fetch()
                with self._lock:
                    self._state = new
                self.error = None
                self.failures = 0
                self.last_ok = time.time()
            except Exception as exc:  # noqa: BLE001 - a source must never take the loop down
                self.failures += 1
                self.error = f"{type(exc).__name__}: {exc}"[:120]
                if self.failures in (1, 5, 50):
                    log.warning("%s failed (%d): %s", self.name, self.failures, self.error)
            wait = self.interval if self.failures == 0 else min(self.interval, 5 * self.failures)
            self._stop.wait(max(0.2, wait - (time.monotonic() - started)))

    def stop(self) -> None:
        self._stop.set()


class StaticSource:
    """Fixed state, for tests and previews."""

    def __init__(self, state: dict, error: str | None = None):
        self._state = state
        self.error = error
        self.last_ok = time.time() if state else 0.0

    @property
    def state(self) -> dict:
        return copy.copy(self._state)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass
