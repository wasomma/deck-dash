# deck-dash

A 15-key Stream Deck (gen-1, 3x5) as an ambient information display: live tiles for the
clock, weather, GPU, CPU, network, a Bitaxe miner, GitHub CI, VPS health and the Windows
crash log, a news ticker across the bottom row, a full-deck zoom view on press, and
(Phase 3) full-deck animations when idle. It drives the deck directly over HID; the Elgato
software is not needed and must not be running.

## Setup

1. `python -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt`
2. Put `hidapi.dll` (x64, from the libusb/hidapi GitHub releases, `hidapi-win.zip`) in
   `C:\Users\WesF\Desktop\Dev\Tools\hidapi\` (path configurable as `deck.hidapi_dir`).
3. Benchmark the unit once: `.venv\Scripts\python tools\bench.py`
4. Run: `.venv\Scripts\python -m deckdash`
5. Run at logon (per-user Task Scheduler entry, restarts on failure, no admin needed), from
   your own terminal: `powershell -ExecutionPolicy Bypass -File tools\install_task.ps1`
   (`-Status` to inspect, `-Remove` to unregister). The task runs `pythonw.exe`, so all
   output goes to `logs\deckdash.log`. A second copy started by hand exits immediately.

No hardware handy: `.venv\Scripts\python -m deckdash --sim --seconds 15` writes
`sim/canvas.png` every second; write a key index to `sim/press.txt` to simulate a press.
`.venv\Scripts\python tools\preview.py` renders the board and every zoom view with live
data into `sim/board.png` and `sim/zoom-*.png`.

## Layout

`config.toml` `[layout] keys` lists 15 tile names, row-major from the top-left key. Empty
string = dark key. The default board:

| | col 1 | col 2 | col 3 | col 4 | col 5 |
|---|---|---|---|---|---|
| row 1 | `clock` | `weather` | `forecast` (next 5 h) | `gpu` | `cpu` |
| row 2 | `net` | `bitaxe` | `ci` | `vps` | `bsod` |
| row 3 | `news` marquee across all five keys | | | | |

Press any tile for its zoom view (10 s, any press returns). The news zoom shows five
headlines, one per column; pressing a column opens that story in the browser.

Machine-local values go in `config.local.toml` (gitignored): the weather location (looked
up once from the public IP and written automatically), the Bitaxe LAN address, and the
`[vps]` block (an SSH alias plus one `[[vps.services]]` table per service with `name`,
`label`, `unit`, `port`, `local` and `public` URLs). Every table in `config.toml` can be
overridden there key by key.

## Data sources

- Weather: Open-Meteo, no key. GPU: NVML. CPU/RAM/disk/net: psutil. WAN latency: `ping`.
- Bitaxe: AxeOS `GET /api/system/info` every 10 s.
- CI: `gh api` (the logged-in GitHub CLI) for the latest workflow run and open PRs of each
  configured repo, every 2 min (30 s while a run is in progress).
- VPS: one SSH round trip per minute runs `systemctl is-active` and a local curl per
  service on the box; a public HTTPS GET from this PC measures edge latency.
- BSOD watch: `wevtutil` reads Kernel-Power 41 and BugCheck 1001 from the System log; the
  tile shows time since the last bugcheck, the 30-day count and uptime, with a 30-day strip.
- News: feedparser over the configured RSS feeds every 10 min.

## Layout of the code

- `deckdash/device.py`: real deck and simulator; only keys whose pixels changed are sent.
- `deckdash/sources/`: background pollers, one per data source.
- `deckdash/tiles/`: one file per tile; `render()` is a 72x72 image, `render_zoom()`
  fifteen; a wide tile (`width > 1`, the ticker) renders its keys together in `render_span()`.
- `deckdash/canvas.py`: virtual full-deck canvas including bezel gaps. Text never straddles
  a bezel (`Canvas.key_text`); only graphics may span keys. The marquee is the deliberate
  exception.
- `deckdash/app.py`: the render loop.
- `tools/install_task.ps1`: the logon task.

Tests: `.venv\Scripts\python -m pytest -q`
