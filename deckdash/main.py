from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from . import __version__, config
from .app import App
from .device import RealDeck, SimDeck

ROOT = Path(__file__).resolve().parent.parent


def setup_logging(verbose: bool) -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(log_dir / "deckdash.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="deckdash", description="Stream Deck ambient info display")
    p.add_argument("--sim", action="store_true", help="no hardware: write sim/canvas.png instead")
    p.add_argument("--seconds", type=float, default=0.0, help="run for this long, then exit (0 = forever)")
    p.add_argument("--config", default=None, help="path to config.toml")
    p.add_argument("--hidapi", default=None, help="folder containing hidapi.dll (overrides config)")
    p.add_argument("--gap", type=int, default=None, help="bezel gap in px (overrides config)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"deckdash {__version__}")
    args = p.parse_args(argv)

    setup_logging(args.verbose)
    cfg = config.load(args.config)
    dk = cfg.setdefault("deck", {})
    if args.gap is not None:
        dk["gap_px"] = args.gap
    if args.sim:
        deck = SimDeck(gap=int(dk.get("gap_px", 24)), out_dir=ROOT / "sim")
    else:
        deck = RealDeck(hidapi_dir=args.hidapi or dk.get("hidapi_dir"), brightness=int(dk.get("brightness", 80)))
    app = App(cfg, deck)
    logging.getLogger(__name__).info("deckdash %s starting (%s)", __version__, "simulator" if args.sim else "hardware")
    app.run(max_seconds=args.seconds or None)
    return 0
