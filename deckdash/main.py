from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from . import __version__, config
from .app import App
from .device import RealDeck, SimDeck

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)

_mutex_handle = None


def setup_logging(verbose: bool) -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(log_dir / "deckdash.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # pythonw.exe has no console
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def single_instance(name: str = "Local\\deck-dash") -> bool:
    """Hold a named mutex so a second copy (manual run next to the scheduled task) exits early."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def open_with_retry(deck: RealDeck, retry_s: float = 10.0) -> None:
    """Wait for the deck rather than crash: at logon the USB stack may still be waking up."""
    attempt = 0
    while True:
        try:
            deck.open()
            return
        except Exception as exc:  # noqa: BLE001
            attempt += 1
            if attempt in (1, 6, 30) or attempt % 360 == 0:
                log.warning("deck not available (attempt %d): %s", attempt, exc)
            time.sleep(retry_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="deckdash", description="Stream Deck ambient info display")
    p.add_argument("--sim", action="store_true", help="no hardware: write sim/canvas.png instead")
    p.add_argument("--seconds", type=float, default=0.0, help="run for this long, then exit (0 = forever)")
    p.add_argument("--config", default=None, help="path to config.toml")
    p.add_argument("--hidapi", default=None, help="folder containing hidapi.dll (overrides config)")
    p.add_argument("--gap", type=int, default=None, help="bezel gap in px (overrides config)")
    p.add_argument("--ambient", default=None, metavar="SCENE", help="start in this ambient scene and return to it 5 s after any press")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"deckdash {__version__}")
    args = p.parse_args(argv)

    setup_logging(args.verbose)
    if not args.sim and not single_instance():
        log.error("another deck-dash is already running (scheduled task?); exiting")
        return 3
    cfg = config.load(args.config)
    dk = cfg.setdefault("deck", {})
    if args.gap is not None:
        dk["gap_px"] = args.gap
    if args.sim:
        deck = SimDeck(gap=int(dk.get("gap_px", 24)), out_dir=ROOT / "sim")
    else:
        deck = RealDeck(hidapi_dir=args.hidapi or dk.get("hidapi_dir"), brightness=int(dk.get("brightness", 80)))
        open_with_retry(deck)
    if args.ambient is not None:
        dk["idle_minutes"] = 5 / 60
    app = App(cfg, deck)
    if args.ambient is not None:
        app.forced_scene = args.ambient
        app.start_ambient(time.time(), args.ambient)
    log.info("deckdash %s starting (%s)", __version__, "simulator" if args.sim else "hardware")
    app.run(max_seconds=args.seconds or None)
    return 0
