"""Phase 0: measure how fast this exact Stream Deck accepts key images.

Usage:  .venv\\Scripts\\python tools\\bench.py [hidapi_dir]
Prints device identity, ms per single-key update, ms per full 15-key frame, and listens
for key presses for five seconds so the index mapping can be checked.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from deckdash import config  # noqa: E402
from deckdash.device import add_hidapi_dir  # noqa: E402


def noise(size):
    w, h = size
    data = bytes(random.getrandbits(8) for _ in range(w * h * 3))
    return Image.frombytes("RGB", size, data)


def main() -> int:
    cfg = config.load()
    hid_dir = sys.argv[1] if len(sys.argv) > 1 else cfg["deck"].get("hidapi_dir")
    add_hidapi_dir(hid_dir)
    from StreamDeck.DeviceManager import DeviceManager
    from StreamDeck.ImageHelpers import PILHelper

    to_native = getattr(PILHelper, "to_native_key_format", None) or PILHelper.to_native_format
    decks = DeviceManager().enumerate()
    if not decks:
        print("no Stream Deck found")
        return 1
    deck = decks[0]
    deck.open()
    deck.reset()
    fmt = deck.key_image_format()
    print(f"type      : {deck.deck_type()}")
    print(f"serial    : {deck.get_serial_number()}")
    print(f"firmware  : {deck.get_firmware_version()}")
    print(f"keys      : {deck.key_count()}")
    print(f"format    : {fmt}")
    deck.set_brightness(60)

    frames = [noise(fmt["size"]) for _ in range(30)]
    t0 = time.perf_counter()
    natives = [to_native(deck, f) for f in frames]
    conv_ms = (time.perf_counter() - t0) / len(frames) * 1000
    print(f"convert   : {conv_ms:.1f} ms per key image ({len(natives[0])} bytes each)")

    n = 60
    t0 = time.perf_counter()
    for i in range(n):
        deck.set_key_image(0, natives[i % 30])
    single_ms = (time.perf_counter() - t0) / n * 1000
    print(f"single key: {single_ms:.1f} ms per update ({1000 / single_ms:.1f} keys/s)")

    rounds = 10
    keys = deck.key_count()
    t0 = time.perf_counter()
    for r in range(rounds):
        for k in range(keys):
            deck.set_key_image(k, natives[(r * keys + k) % 30])
    full_ms = (time.perf_counter() - t0) / rounds * 1000
    print(f"full deck : {full_ms:.0f} ms per {keys}-key frame ({1000 / full_ms:.1f} fps)")

    presses = []

    def cb(_d, key, state):
        presses.append((key, state))
        print(f"  key {key} {'down' if state else 'up'}")

    deck.set_key_callback(cb)
    print("press some keys (5 s)...")
    time.sleep(5)
    print(f"events    : {len(presses)}")
    deck.reset()
    deck.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
