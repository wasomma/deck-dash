# deck-dash

A 15-key Stream Deck (gen-1, 3x5) as an ambient information display: live tiles for the
clock, weather, GPU, CPU, network and more, a full-deck zoom view on press, and (Phase 3)
full-deck animations when idle. It drives the deck directly over HID; the Elgato software
is not needed and must not be running.

## Setup

1. `python -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt`
2. Put `hidapi.dll` (x64, from the libusb/hidapi GitHub releases, `hidapi-win.zip`) in
   `C:\Users\WesF\Desktop\Dev\Tools\hidapi\` (path configurable as `deck.hidapi_dir`).
3. Benchmark the unit once: `.venv\Scripts\python tools\bench.py`
4. Run: `.venv\Scripts\python -m deckdash`

No hardware handy: `.venv\Scripts\python -m deckdash --sim --seconds 15` writes
`sim/canvas.png` every second; write a key index to `sim/press.txt` to simulate a press.

## Layout

`config.toml` `[layout] keys` lists 15 tile names, row-major from the top-left key.
Tiles: `clock`, `weather`, `forecast`, `gpu`, `cpu`, `net`. Empty string = dark key.
Machine-local values (weather location, looked up once from the public IP) are written to
`config.local.toml`, which is gitignored.

## Layout of the code

- `deckdash/device.py`: real deck and simulator; only keys whose pixels changed are sent.
- `deckdash/sources/`: background pollers (weather, psutil, NVML, ping).
- `deckdash/tiles/`: one file per tile; `render()` is a 72x72 image, `render_zoom()` fifteen.
- `deckdash/canvas.py`: virtual full-deck canvas including bezel gaps.
- `deckdash/app.py`: the render loop.

Tests: `.venv\Scripts\python -m pytest -q`
