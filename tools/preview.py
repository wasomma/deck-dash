"""Render the board and every zoom view with live data into sim/*.png (no hardware needed).

Usage:  .venv\\Scripts\\python tools\\preview.py [--wait SECONDS] [--gap PX]
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
from deckdash.ambient import SCENES, make_scene  # noqa: E402
from deckdash.app import App  # noqa: E402
from deckdash.canvas import Canvas  # noqa: E402
from deckdash.device import SimDeck  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wait", type=float, default=6.0, help="seconds to let the pollers fetch data")
    p.add_argument("--gap", type=int, default=None)
    p.add_argument("--scene-seconds", type=float, default=4.0, help="scene time to simulate before the ambient snapshot")
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
    for tile in app.tiles:
        if not tile.zoomable:
            continue
        app._on_press(tile.slot, True)
        app.tick()
        deck.flush(10.0)
        target = out / f"zoom-{tile.name.replace("|", "-")}.png"
        deck.write().replace(target)
        print(f"wrote {target}")
        app.zoom = None  # back to the board without triggering the zoom-press hook
        app._invalidate()
        app.tick()
        deck.flush(10.0)
    for name in SCENES:
        scene = make_scene(name, cfg, app.sources, seed=7)
        t0 = time.time()
        frames = int(scene.fps * args.scene_seconds)
        t1 = time.perf_counter()
        for k in range(frames + 1):
            images = scene.frame(t0 + k / scene.fps)
        ms = (time.perf_counter() - t1) / (frames + 1) * 1000
        target = out / f"ambient-{name}.png"
        Canvas.compose(images, gap, scale=2).save(target)
        print(f"wrote {target} ({ms:.1f} ms per frame)")
    app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
