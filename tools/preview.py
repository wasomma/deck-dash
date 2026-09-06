"""Render the board and every zoom view with live data into sim/*.png (no hardware needed).

Usage:  .venv\\Scripts\\python tools\\preview.py [--wait SECONDS]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deckdash import config  # noqa: E402
from deckdash.app import App  # noqa: E402
from deckdash.device import SimDeck  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wait", type=float, default=6.0, help="seconds to let the pollers fetch data")
    p.add_argument("--gap", type=int, default=None)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    gap = args.gap if args.gap is not None else int(cfg["deck"].get("gap_px", 24))
    out = ROOT / "sim"
    deck = SimDeck(gap=gap, out_dir=out, interval=1e9)
    app = App(cfg, deck)
    app.start()
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        app.tick()
        time.sleep(app.tick_s)
    board = out / "board.png"
    deck.write().replace(board)
    print(f"wrote {board}")
    for idx, tile in enumerate(app.slots):
        if tile is None or not tile.zoomable:
            continue
        app._on_press(idx, True)
        app.tick()
        deck.flush(10.0)
        target = out / f"zoom-{tile.name}.png"
        deck.write().replace(target)
        print(f"wrote {target}")
        app._on_press(idx, True)  # back to the board
        app.tick()
        deck.flush(10.0)
    app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
