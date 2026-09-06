# deck-dash phases

Plan of record: `~/.claude/plans/so-i-actually-own-crystalline-pike.md` (approved 2026-09-06, all defaults).
A fresh session resumes from this file.

## Facts fixed on 2026-09-06
- Device: original gen-1 Stream Deck (USB 0FD9:0060), 15 keys, 72x72, raw BMP over HID.
- Elgato software not installed; nothing else holds the device.
- Stack: Python 3.13 venv in `.venv`, python-elgato-streamdeck 0.10.0, Pillow 12.3, numpy, psutil, nvidia-ml-py, feedparser, requests, pytest.
- `hidapi.dll` (x64) must live in `C:\Users\WesF\Desktop\Dev\Tools\hidapi\`; `deckdash.device.add_hidapi_dir` registers it with `os.add_dll_directory` before the library imports.

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
- [x] `device.py`: dirty-key hashing, per-tick byte budget with round-robin carry-over, reconnect on write failure, `SimDeck` writing `sim/canvas.png` and taking presses from `sim/press.txt`.
- [x] `sources/`: weather (Open-Meteo, IP-located once, cached to `config.local.toml`), sys (psutil), gpu (NVML), ping.
- [x] `tiles/`: clock, weather now, 6-hour forecast strip, GPU, CPU, net, each with a full-deck zoom view.
- [x] `app.py`: 10 Hz tick, per-tile refresh, zoom on press with 10 s timeout, night brightness.
- [x] pytest suite (`tests/`), 39 tests.
- [x] Simulator-verified 2026-09-06: `tools/preview.py` renders `sim/board.png` and `sim/zoom-*.png` with live data.
- [ ] Wes eyeballs the physical deck (needs Phase 0).

Design rule learned from the first zoom renders: **text never straddles a bezel; only graphics
(lines, fills, bars, big shapes) may span keys.** `Canvas.key_text` enforces it; use it for every
label in a zoom or ambient view.

Follow-ups: psutil reports the nominal 3.0 GHz on Windows, so the CPU tile shows RAM instead of a
clock; a live clock needs the PDH counter `% Processor Performance` (ctypes, no extra package).

Run: `.venv\Scripts\python -m deckdash --sim --seconds 15` then open `sim/canvas.png`;
previews: `.venv\Scripts\python tools\preview.py`; hardware: `.venv\Scripts\python -m deckdash`.

## Phase 2 — remaining tiles + zoom + Task Scheduler
- [ ] Bitaxe (10.0.0.191 `/api/system/info`), CI (`gh run list` / `gh pr list` for fpv-sim x4, drift-duet, guild-mp), VPS health (URLs from the `guild-vps-deploy` skill), BSOD watch (System log 41/1001 + uptime), news ticker (HN, Ars, BBC World; press = 5 headlines; press a headline = open in browser).
- [ ] Task Scheduler registration script (gate: Wes approves).

## Phase 3 — ambient
- [ ] Bezel-gap calibration (`--gap`), weather-as-ambient, plasma, Life, Matrix rain, aquarium; idle 10 min; off on lock.

## Phase 4 — alerts and the interesting buttons
- [ ] Toast overlay; Claude-needs-you tile via hooks (gate: `settings.json` edit through the update-config skill); now-playing tile (Windows media session API).
