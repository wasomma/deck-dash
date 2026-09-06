# deck-dash phases

Plan of record: `~/.claude/plans/so-i-actually-own-crystalline-pike.md` (approved 2026-09-06, all defaults).
A fresh session resumes from this file.

## Facts fixed on 2026-09-06
- Device: original gen-1 Stream Deck (USB 0FD9:0060), 15 keys, 72x72, raw BMP over HID.
- Elgato software not installed; nothing else holds the device.
- Stack: Python 3.13 venv in `.venv`, python-elgato-streamdeck 0.10.0, Pillow 12.3, numpy, psutil, nvidia-ml-py, feedparser, requests, pytest.
- `hidapi.dll` (x64) must live in `C:\Users\WesF\Desktop\Dev\Tools\hidapi\`; `deckdash.device.add_hidapi_dir` registers it with `os.add_dll_directory` before the library imports.
- Worktree sessions: the venv lives only in the main checkout; run `C:\Users\WesF\Desktop\Dev\Projects\deck-dash\.venv\Scripts\python` from the worktree directory. `config.local.toml` must be copied into the worktree by hand (gitignored).

## Phase 0 — spike: open the deck and benchmark it
Status: **done 2026-09-06.** `hidapi.dll` 0.15.0 x64 (166,912 bytes) installed in `Desktop\Dev\Tools\hidapi\` (Wes approved the download).
- [x] `tools/bench.py` written: identity, ms per key, ms per full frame, key-press echo.
- [x] Run on the unit; numbers below. `flush_budget_ms = 60` covers a full repaint in one tick.

Results (serial AL19H1A00539, firmware 1.0.191203, BMP 72x72 flipped both axes):

| Measure | Value |
|---|---|
| image convert (PIL -> BMP) | 0.8 ms per key |
| single key update | 4.0 ms (about 250 keys/s) |
| full 15-key frame | 60 ms (16.5 fps) |

So the gen-1 bus is not the constraint the plan feared: full-deck animation at 12-15 fps is
realistic, and dirty-key flushing is a nicety rather than a necessity. Phase 3 can target
`anim_fps = 12` for ambient scenes.

## Phase 1 — core loop + first tiles (simulator-verified)
Status: **done 2026-09-06**, reviewed on the hardware by Wes ("looks great").
- [x] `device.py`: dirty-key hashing, per-tick byte budget with round-robin carry-over, reconnect on write failure, `SimDeck` writing `sim/canvas.png` and taking presses from `sim/press.txt`.
- [x] `sources/`: weather (Open-Meteo, IP-located once, cached to `config.local.toml`), sys (psutil), gpu (NVML), ping.
- [x] `tiles/`: clock, weather now, 5-hour forecast strip, GPU, CPU, net, each with a full-deck zoom view.
- [x] `app.py`: 10 Hz tick, per-tile refresh, zoom on press with 10 s timeout, night brightness.
- [x] pytest suite (`tests/`).
- [x] Simulator-verified: `tools/preview.py` renders `sim/board.png` and `sim/zoom-*.png` with live data.
- [x] Wes eyeballed the physical deck.

Design rule learned from the first zoom renders: **text never straddles a bezel; only graphics
(lines, fills, bars, big shapes) may span keys.** `Canvas.key_text` enforces it; use it for every
label in a zoom or ambient view. The news marquee is the one deliberate exception (motion carries
the eye across the gap).

Follow-ups: psutil reports the nominal 3.0 GHz on Windows, so the CPU tile shows RAM instead of a
clock; a live clock needs the PDH counter `% Processor Performance` (ctypes, no extra package).

Run: `.venv\Scripts\python -m deckdash --sim --seconds 15` then open `sim/canvas.png`;
previews: `.venv\Scripts\python tools\preview.py`; hardware: `.venv\Scripts\python -m deckdash`.

## Phase 2 — remaining tiles + zoom + Task Scheduler
Status: **code done 2026-09-06** (64 tests pass, previews checked, running on the hardware from the
worktree). The Task Scheduler registration is a gate for Wes (below).
- [x] Bitaxe tile (`sources/bitaxe.py`, `tiles/bitaxe.py`): AxeOS `/api/system/info` every 10 s; swinging pickaxe while hashing; zoom = live numbers, 10-min hashrate line, shares/uptime/clock/fan/pool. `new_best` flag is kept for the Phase 4 toast.
- [x] CI board (`sources/ci.py`, `tiles/ci.py`): `gh api` runs + open PRs per repo (six repos, labels in `config.toml`), 2 min / 30 s while running, six repos fetched in parallel; dot per repo, pulsing amber while a run is in progress; zoom = repo cards, a "latest" 2x2 spotlight (failure > running > most recent), open PRs on row 3.
- [x] VPS health (`sources/vps.py`, `tiles/vps.py`): one SSH round trip per minute (`systemctl is-active` + local curl per service on the box) plus a public HTTPS GET per service for edge latency; ok/degraded/down/unknown; zoom = one row per service with a 30-min latency line. Hosts live only in `config.local.toml`.
- [x] BSOD watch (`sources/bsod.py`, `tiles/bsod.py`): `wevtutil` query for Kernel-Power 41 + WER 1001, merged into crash records (bugcheck vs power loss); tile = time since last bugcheck, 30-day bugcheck count, last code, uptime, 30-day strip; zoom = counters + last ten events.
- [x] News ticker (`sources/news.py`, `tiles/news.py`): first wide tile (`width = 5`, `render_span`); marquee at 60 px/s, 10 fps, seamless wrap, new headlines swapped in at the wrap; zoom = five headlines in five columns starting from the one under the window centre; pressing a column opens the story (`webbrowser.open`). App gained `build_slots` (wide tiles) and `on_zoom_press`.
- [x] `main.py`: named-mutex single instance, `open_with_retry` (deck absent at logon), no stderr handler under `pythonw`.
- [x] `tools/install_task.ps1` (ASCII, parse-checked): at-logon task for the current user, 20 s delay, restart every minute up to 99 times, unlimited run time, `IgnoreNew`; refuses to register if `hidapi.dll` is not visible; `-Status` / `-Remove`.
- [ ] **Gate (Wes):** from his own terminal, `Test-Path C:\Users\WesF\Desktop\Dev\Tools\hidapi\hidapi.dll` (a sandboxed install could have been virtualized), then `powershell -ExecutionPolicy Bypass -File tools\install_task.ps1`. The script stops any hand-started copy first.

Observations from the live run on 2026-09-06: the System log shows 5 bugchecks and 12 power-loss
reboots in the last 30 days (the latter are Kernel-Power 41 with BugcheckCode 0, mostly
`SleepInProgress` != 0), so the tile headlines bugchecks and lists both. `gh api` for six repos
takes about 4 s in parallel; the SSH probe about 1 s; wevtutil 20 ms.

## Phase 3 — ambient
Status: **code done 2026-09-06** (previews in `sim/ambient-*.png`, all scenes under 3 ms per frame at 456x264). Gap calibration is a gate for Wes (below).
- [x] `ambient/` package: `Scene` base (fresh instance per showing, `frame(now)` -> 15 keys), scenes `weather` (sky by real sunrise/sunset, clouds drift with the wind, rain/snow/fog/lightning by condition, time/temp/date in dark pills inside single keys), `plasma` (numpy sine fields, quarter-res, palette per showing), `life` (8 px cells, age colour, reseeds when still), `matrix` (half-width katakana from MS Gothic, sprite atlas), `aquarium` (six fish, bubbles, weeds, crab).
- [x] `app.py`: idle timer -> ambient after `deck.idle_minutes`; scenes rotate every `ambient.scene_minutes` in `ambient.scenes` order; any press wakes the board (never zooms); `ambient.fps` ceiling (8). `--ambient SCENE` starts in a scene for checking.
- [x] Lock: `session.py` polls the input desktop every 5 s; two positives in a row (so a UAC prompt does not count) -> brightness 0 and no rendering; unlock restores brightness and the board. Night brightness unchanged (30% 22:00-07:00).
- [x] `tools/calibrate.py`: circle, diagonals and cross drawn across the whole canvas on the real deck; keys 0/4 = gap -1/+1, 5/9 = -4/+4, 14 saves `deck.gap_px` to `config.local.toml`, 10 quits. `install_task.ps1` gained `-Stop` / `-Start` so the device can be freed for it.
- [ ] **Gate (Wes):** free the deck (`tools\install_task.ps1 -Stop`, which also kills a hand-started copy), run `.venv\Scripts\python tools\calibrate.py`, adjust until the lines run straight through the bezels, press key 14 to save; then `-Start` (or run deck-dash by hand). The ticker and every scene use the saved gap on the next start.
- [ ] Wes eyeballs the scenes on the hardware (idle 10 min, or `.venv\Scripts\python -m deckdash --ambient aquarium`).

## Phase 4 — alerts and the interesting buttons
Status: **code done 2026-09-06** (92 tests). Two gates for Wes (below).
- [x] `alerts.py`: `AlertWatcher` diffs the sources once a second: CI fail/pass per repo, miner offline/back (45 s without an answer) and new best difficulty, VPS service down/degraded/up, new bugcheck or power-loss reboot (last seen time persisted in `state/alerts.json`, so a crash toasts once the PC is back and history is never replayed), Claude session waiting. `render_toast` = 5 s full-deck toast (ripple, or sparkles for a record) with kind / title / subject in row 1 and the detail wrapped on row 2; `draw_badge` = corner dot on the owning tile until it is pressed; a recovery ("good") alert clears the badge. Toasts interrupt zoom and ambient, wait while locked, and a press dismisses without a badge.
- [x] Overlays: `[layout] overlays = { forecast = "nowplaying", net = "claude" }`. `OverlayTile` shows the top tile while `active(now)` and the base tile otherwise (zoom, press and badge follow whichever is showing).
- [x] Claude-needs-you: `tools/claude_hook.py` appends hook payloads to `state/claude-events.jsonl`; `sources/claude.py` tails it into per-session state (busy / waiting / idle, project from cwd, worktrees shown as `repo/wt`); `tiles/claude.py` = one dot per session (amber pulse while waiting), zoom = one key per session with the message; press acknowledges. Setup and the settings snippet: `docs/claude-hooks.md`.
- [x] Now playing: `tools/media_watch.ps1` (PowerShell WinRT, no new packages) streams the Windows media session as JSON lines, album art only when it changes, commands through `state/media-cmd.txt`; `sources/media.py` keeps the helper alive; `tiles/nowplaying.py` = art background, sliding title, artist, progress; zoom = art on a 2x2 block, title/artist/album, progress across row 3, keys 10/12/14 = previous / play-pause / next (stays zoomed). `nowplaying.ignore_apps` skips browser sessions if wanted (verified live: an Edge tab playing a stream shows up as "MSEdge").
- [ ] **Gate (Wes):** hooks. Say the word and I merge the five hook entries from `docs/claude-hooks.md` into `~/.claude/settings.json` through the update-config skill (Read + Edit, then a JSON validation), or paste them yourself. Until then the tile says "hooks off" and stays hidden behind `net`.
- [ ] Wes eyeballs a toast on the hardware. No hooks needed for a demo: `python tools\claude_hook.py --event Notification --session demo --message "hello"` fires the CLAUDE toast within two seconds; `--event SessionEnd --session demo` removes the dot again.

Out of scope by decision: audio-reactive bars.

## Merge and push
Phases 2-4 are commits on `claude/sweet-mclean-8f59b9` (worktree). On "push it": fast-forward `main` to the branch and push; the main checkout then needs `git pull` before the scheduled task runs from it.
