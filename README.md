# deck-dash

A 15-key Stream Deck (gen-1, 3x5) as an ambient information display: live tiles for the
clock, weather, GPU, CPU, network, a Bitaxe miner, GitHub CI, VPS health and the Windows
crash log, a news ticker across the bottom row, a full-deck zoom view on press, and
six full-deck scenes when idle. It drives the deck directly over HID; the Elgato
software is not needed and must not be running.

## Setup

1. `python -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt`
2. Put `hidapi.dll` (x64, from the libusb/hidapi GitHub releases, `hidapi-win.zip`) in
   `C:\Users\WesF\Desktop\Dev\Tools\hidapi\` (path configurable as `deck.hidapi_dir`).
3. Benchmark the unit once: `.venv\Scripts\python tools\bench.py`
4. Run: `.venv\Scripts\python -m deckdash`
5. Run at logon (per-user Task Scheduler entry, restarts on failure, no admin needed), from
   your own terminal: `powershell -ExecutionPolicy Bypass -File tools\install_task.ps1`
   (`-Status` to inspect, `-Stop` and `-Start` to free the deck for a tool and take it back, `-Remove` to unregister). The task runs without a console, so all
   output goes to `logs\deckdash.log`.
6. Optional, and what the task prefers once it is there: `.venv\Scripts\python -m pip install -e .`
   from the checkout, which puts `deckdash` and `deckdashw` on the venv's `Scripts`. Re-run
   `install_task.ps1` afterwards so the task launches `deckdashw.exe`.

At start the app raises itself to normal CPU class and memory priority and opts out of Windows
power throttling (a windowless task-launched process gets efficiency QoS otherwise, which
stalled the USB flushes); the first log line says what it found and set, plus the job
limits. Any loop step slower than `deck.slow_step_ms` (100 ms) is logged as a warning that
names the step; the once-a-minute ambient fps line ends with the slow-tick count.

No hardware handy: `.venv\Scripts\python -m deckdash --sim --seconds 15` writes
`sim/canvas.png` every second; write a key index to `sim/press.txt` to simulate a press.
`.venv\Scripts\python tools\preview.py` renders the board and every zoom view with live
data into `sim/board.png` and `sim/zoom-*.png`.

## Using it

One process owns the deck and shows three other faces: a tray icon, a dashboard page, and a
command. All three reach the render loop through the same control channel, the named pipe
`\\.\pipe\deckdash`, so every deck write stays on the one thread.

**Tray icon.** A 3x5 grid of dots, green normally, grey while paused or locked, amber while an
alert badge is waiting. Right-click for the menu: wake the board, pick a scene, pause/resume,
brightness, open the dashboard, open the log, edit `config.local.toml`, recalibrate the bezel
gap, restart, quit. Double-click opens the dashboard. `[ui] tray = false` turns it off.

**Dashboard.** `http://127.0.0.1:8770` (`[ui] dashboard_port`), loopback only, opened as its own
window with Edge in app mode. It shows the deck as it looks now (the fifteen key images composed
with the calibrated gap, refreshed five times a second), the status the `status` command reports,
every source's age and last error, buttons for the same things the tray offers, and a settings
form for the layout, the overlays, the scene rotation, the brightness schedule and the news and CI
lists. Saves go to `config.local.toml` only, so `config.toml` stays canonical, and the page says
when a change needs a restart rather than a reload (the news and CI pollers read their lists once,
at start). Hosts and addresses are not shown and never written. Open it with the tray, with
`deckdash ctl open`, or by launching deck-dash again while a copy is running.

**Command.** `deckdash ctl <command>` talks to the running copy:

    deckdash ctl status                  # mode, scene, fps, slow ticks, every source's age
    deckdash ctl scene tokyo             # or wake, next, pause, resume
    deckdash ctl brightness 50           # a fixed level, or `auto` for the day/night schedule
    deckdash ctl toast test hello "..."  # a test toast through the real alert path
    deckdash ctl reload                  # re-read the config: layout, scenes, brightness, idle
    deckdash ctl open                    # the dashboard window
    deckdash ctl restart                 # install_task.ps1 -Stop then -Start
    deckdash ctl quit

`--json` prints the raw reply. Without the editable install the same thing is
`python -m deckdash ctl ...`. The simulator answers on its own pipe (`--pipe deckdash-sim`) and
its own port, one above the configured one, so it can run beside the live copy.

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

After ten minutes without a press the deck plays ambient scenes (`[ambient]` in
`config.toml`): weather (the sky outside, with the time and temperature), plasma, Conway's
Life, Matrix rain, an aquarium and a Tokyo night drive (an R34 Skyline through neon Shibuya), five minutes each. Any press brings the board back.
Brightness drops to 30% from 22:00 to 07:00 and the deck goes dark while the Windows session
is locked. `python -m deckdash --ambient aquarium` starts straight into a scene.

Alerts interrupt whatever is showing with a five-second full-deck toast, then leave a dot on
the owning tile until it is pressed: a CI run fails (and passes again), the Bitaxe drops off
the LAN (and returns) or sets a new best difficulty, a VPS service goes down (and comes back),
a new bugcheck appears in the System log after a reboot, a Claude Code session needs input.

Two tiles overlay others only while they have something to show (`[layout] overlays`): the
now-playing tile takes the forecast key while music plays (album art, sliding title, progress;
zoom has previous / play-pause / next on the bottom row) and the Claude tile takes the net key
while a Claude Code session is working, waiting for you, or just finished. The Claude tile
needs five hooks in `~/.claude/settings.json`; see `docs/claude-hooks.md`.

Bezel gap: the scenes and the ticker draw on a virtual canvas that includes the gaps between
keys. Calibrate it once on the real deck with `.venv\Scripts\python tools\calibrate.py`
(free the device first: `tools\install_task.ps1 -Stop`); key 14 saves `gap_px` to
`config.local.toml`.

Machine-local values go in `config.local.toml` (gitignored): the weather location (looked
up once from the public IP and written automatically), the Bitaxe LAN address, and the
`[vps]` block (an SSH alias plus one `[[vps.services]]` table per service with `name`,
`label`, `unit`, `port`, `local` and `public` URLs). Every table in `config.toml` can be
overridden there key by key.

## Data sources

- Weather: Open-Meteo, no key. GPU: NVML. CPU/RAM/disk/net: psutil. WAN latency: `ping`.
- CPU clock: the PDH counter `% Processor Performance` times `Processor Frequency` (ctypes, no
  package), because psutil reports the nominal clock on Windows.
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
- `deckdash/ambient/`: one file per scene; a scene paints the whole canvas per frame.
- `deckdash/alerts.py`: alert detection, the toast, the badge.
- `deckdash/session.py`: Windows lock detection (polled).
- `deckdash/app.py`: the render loop: board, zoom, toast, ambient, locked.
- `tools/install_task.ps1`: the logon task; `tools/calibrate.py`: bezel-gap calibration;
  `tools/media_watch.ps1`: the media-session helper; `tools/claude_hook.py`: the hook.

Tests: `.venv\Scripts\python -m pytest -q`
