"""Bezel-gap calibration on the real deck.

Stop deck-dash first (``tools\\install_task.ps1 -Stop`` or Ctrl-C the terminal copy), then:

    .venv\\Scripts\\python tools\\calibrate.py

The deck shows a circle, diagonals and a horizontal line drawn across the whole canvas at the
current gap. Adjust until the lines run straight through the bezels and the circle is round:

    key 0: gap -1     key 4: gap +1     key 5: gap -4     key 9: gap +4
    key 10: quit without saving         key 14: save to config.local.toml and quit
"""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deckdash import config  # noqa: E402
from deckdash.canvas import Canvas  # noqa: E402
from deckdash.device import RealDeck  # noqa: E402
from deckdash.gfx import BLUE, DIM, FG, GREEN, RED, WARM  # noqa: E402


def render(gap: int) -> list:
    c = Canvas(gap)
    d = c.draw
    cx, cy = c.w / 2, c.h / 2
    r = c.h / 2 - 6
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=BLUE, width=3)
    d.line([(0, 0), (c.w, c.h)], fill=WARM, width=3)
    d.line([(0, c.h), (c.w, 0)], fill=WARM, width=3)
    d.line([(0, cy), (c.w, cy)], fill=GREEN, width=3)
    d.line([(cx, 0), (cx, c.h)], fill=GREEN, width=3)
    for k in range(1, 5):  # short ticks straddling every vertical bezel
        x = k * (72 + gap) - gap / 2
        d.line([(x - 30, cy - 40), (x + 30, cy - 40)], fill=RED, width=2)
    c.key_text(0, "-1", 20, FG)
    c.key_text(4, "+1", 20, FG)
    c.key_text(5, "-4", 20, FG)
    c.key_text(9, "+4", 20, FG)
    c.key_text(7, f"gap {gap}", 16, FG, where="t", pad=4)
    c.key_text(10, "quit", 16, DIM)
    c.key_text(14, "save", 16, GREEN)
    return c.slice()


def main() -> int:
    cfg = config.load()
    gap = int(cfg["deck"].get("gap_px", 24))
    deck = RealDeck(hidapi_dir=cfg["deck"].get("hidapi_dir"), brightness=int(cfg["deck"].get("brightness", 80)))
    deck.open()
    presses: queue.Queue = queue.Queue()
    deck.on_press(lambda key, down: presses.put(key) if down else None)
    print(f"gap {gap}; press keys on the deck (see the docstring); Ctrl-C quits without saving")
    try:
        while True:
            for i, img in enumerate(render(gap)):
                deck.set_key_image(i, img)
            deck.flush(1.0)
            try:
                key = presses.get(timeout=0.2)
            except queue.Empty:
                continue
            if key == 0:
                gap = max(0, gap - 1)
            elif key == 4:
                gap = min(80, gap + 1)
            elif key == 5:
                gap = max(0, gap - 4)
            elif key == 9:
                gap = min(80, gap + 4)
            elif key == 10:
                print("quit without saving")
                return 0
            elif key == 14:
                config.write_local({"deck": {"gap_px": gap}})
                print(f"saved gap_px = {gap} to {config.LOCAL_CONFIG}")
                return 0
            print(f"gap {gap}")
    except KeyboardInterrupt:
        return 0
    finally:
        deck.close()
        time.sleep(0.2)


if __name__ == "__main__":
    raise SystemExit(main())
