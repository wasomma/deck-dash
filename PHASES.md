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

Results (firmware 1.0.191203, BMP 72x72 flipped both axes):

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
- [x] Wes reviewed the board on the deck 2026-09-06 ("looks great"); zoom presses logged with the expected key indices (top-left origin), so the library's index mapping for the gen-1 unit is confirmed.
- Note: a Bash-tool background process is killed after 10 minutes; until the Task Scheduler entry exists, start the board with PowerShell `Start-Process` (dies with the desktop session, which is acceptable for review).

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
- [x] Task registered by Wes from his own terminal on 2026-09-06 at 19:24 (`tools\install_task.ps1` in the main checkout; the DLL was visible from the real file system). State `Running`; the venv launcher plus the real interpreter show as two `pythonw.exe` entries, which is normal; one media helper runs under the app. To free the deck later: `tools\install_task.ps1 -Stop`, then `-Start`.

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
- [x] Gap calibrated by Wes on 2026-09-06 with `tools\calibrate.py`: `deck.gap_px = 23` saved to `config.local.toml` in the main checkout (placeholder was 24). The scheduled task started afterwards, so the ticker and every scene use it. To recalibrate: `tools\install_task.ps1 -Stop`, run the tool, `-Start`.
- [x] Wes saw the five scenes on the hardware on the evening of 2026-09-06 - that is what prompted the Phase 5 request for a sixth (side view, R34, rain, real glyph signs).
- The `weather` scene described above is the 0.1.0 one, sky only. It was rebuilt on 2026-09-07 to put land under the sky; see "Ambient weather: ground under the sky" at the end of this file.

## Phase 4 — alerts and the interesting buttons
Status: **code done 2026-09-06** (92 tests). Two gates for Wes (below).
- [x] `alerts.py`: `AlertWatcher` diffs the sources once a second: CI fail/pass per repo, miner offline/back (45 s without an answer) and new best difficulty, VPS service down/degraded/up, new bugcheck or power-loss reboot (last seen time persisted in `state/alerts.json`, so a crash toasts once the PC is back and history is never replayed), Claude session waiting. `render_toast` = 5 s full-deck toast (ripple, or sparkles for a record) with kind / title / subject in row 1 and the detail wrapped on row 2; `draw_badge` = corner dot on the owning tile until it is pressed; a recovery ("good") alert clears the badge. Toasts interrupt zoom and ambient, wait while locked, and a press dismisses without a badge.
- [x] Overlays: `[layout] overlays = { forecast = "nowplaying", net = "claude" }`. `OverlayTile` shows the top tile while `active(now)` and the base tile otherwise (zoom, press and badge follow whichever is showing).
- [x] Claude-needs-you: `tools/claude_hook.py` appends hook payloads to `state/claude-events.jsonl`; `sources/claude.py` tails it into per-session state (busy / waiting / idle, project from cwd, worktrees shown as `repo/wt`); `tiles/claude.py` = one dot per session (amber pulse while waiting), zoom = one key per session with the message; press acknowledges. Setup and the settings snippet: `docs/claude-hooks.md`.
- [x] Now playing: `tools/media_watch.ps1` (PowerShell WinRT, no new packages) streams the Windows media session as JSON lines, album art only when it changes, commands through `state/media-cmd.txt`; `sources/media.py` keeps the helper alive; `tiles/nowplaying.py` = art background, sliding title, artist, progress; zoom = art on a 2x2 block, title/artist/album, progress across row 3, keys 10/12/14 = previous / play-pause / next (stays zoomed). `nowplaying.ignore_apps` skips browser sessions if wanted (verified live: an Edge tab playing a stream shows up as "MSEdge").
- [x] Hooks applied on the evening of 2026-09-06 (Wes: "hooks"): the five entries from `docs/claude-hooks.md` are in `~/.claude/settings.json` (user scope, committed in the `~/.claude` config repo). `python` in Git Bash resolves to 3.13 and a pipe-test with real SessionStart / SessionEnd payloads exited 0. Real sessions now feed the tile behind `net`.
- [x] Toast seen on the hardware 2026-09-06 19:25: the demo Notification from `tools\claude_hook.py` toasted within two seconds ("CLAUDE NEEDS YOU deck-dash"), and a SessionEnd for the demo session cleared the dot.

Out of scope by decision: audio-reactive bars.

## Phase 5 — more scenes
Requested by Wes on the evening of 2026-09-06 after seeing the five scenes on the hardware (spec: side view, R34 Skyline, rain on about half the showings, real glyph signs, sixth in the rotation).
- [x] `ambient/tokyo.py` (`tokyo`): side-scrolling Tokyo night drive. Wrapping parallax strips (sky and stars; far skyline with Tokyo Tower, Skytree and a rail viaduct; neon facades with vertical and horizontal signs, shop fronts, rooftop screens; the wet street with lamps, crossings and pedestrians). An R34 Skyline GT-R in Bayside Blue holds the centre with spinning rims, tail-light glow and a headlight beam; a Yamanote train crosses the viaduct every 20-40 s; taxis overtake or get passed in the far lane; about half the showings are rainy (streaks, stronger sign reflections). Sign glyphs are MS Gothic words (Latin fallback without the font) and decorative like the matrix rain, so they cross bezels. About 1.2 ms per frame at 452x262. Appended as the sixth scene; 94 tests.
- [x] Wes on the hardware, 2026-09-07: "tokyo looks great".
- [x] Frame pacing (Wes asked for more fps and chose 14): the loop now sleeps until the next scene or toast frame is due instead of snapping to the 10 Hz tick, which had quantized the 8 fps scenes to 5 fps on the hardware. `ambient.fps = 14`; tokyo, aquarium, matrix, plasma and weather run at 14 (life stays at 4 generations per second); the matrix rain moves per second now so its look is unchanged. Once a minute in ambient mode the log reports the achieved fps and the flush cost. 95 tests.
- [x] Measured on the deck 2026-09-06 20:11 (tokyo, one minute): 13.9 fps at target 14, flush avg 62 ms, max 69 ms, 14.5 keys per frame. The 71 ms frame period holds; 16 would not (bus limit).

## Phase 6 - stall fix, CPU clock, housekeeping
Spec agreed 2026-09-06 21:05 (Wes took all five defaults, asked one by one): (1) measure the once-a-minute stall first, then fix what the instrumentation names; (2) CPU clock via the PDH counter "% Processor Performance" (Processor Information, _Total) with ctypes, replacing the RAM line on the CPU tile, RAM stays in the zoom; (3) delete the two merged local branches and move the sixteen read-only PowerShell allow rules to user scope (list shown before writing, config repo committed); (4) rotation unchanged (five minutes per scene, current order).

Finding that started it: under the scheduled task every scene averages its target but the per-minute flush max is 150-320 ms (weather, plasma, life, matrix, aquarium, tokyo alike), whereas the 20:11 solo run of tokyo from a shell held 66-69 ms. The task's process runs at BelowNormal priority (Task Scheduler default); the solo run was Normal. Sources poll on daemon threads, so a blocking fetch is not the cause.
- [x] Zero-code A/B on the live task process at 21:03: raised to Normal, the tokyo flush max went 187 -> 72 ms and weather 229 -> 68 ms at 13.9 fps while the same Claude sessions kept working. The task ran at BelowNormal (Task Scheduler default priority 7). A flush is about 120 HID writes and every write yields the CPU, so under contention a below-normal process can wait a quantum per write.
- [x] Instrumentation: `App._tick` collects (step, perf_counter) marks; `_report_slow` logs any step over `deck.slow_step_ms` (100) with its name and the sources that completed a fetch during the tick, one warning per 10 s, the rest counted; the ambient fps line ends with the slow-tick count. Validated on the deck from the worktree (tokyo, 21:10-21:18): idle or 8 spinners, either priority: 66-70 ms, 0 slow ticks. 32 spinners (every thread busy): BelowNormal = flush stalls of 5-10 s, 7.4 fps; Normal = 300-700 ms, worst 1.2 s, 8.8 fps, 40 slow ticks a minute. Everyday contention (sessions running git, pytest, PowerShell) is what produced the 150-320 ms spikes at BelowNormal.
- [x] Fix: `main.normal_priority()` raises a below-normal or idle process to normal at start and logs the class; `install_task.ps1` registers with `-Priority 5` (normal). Task restarted from main (6bdc775) at 21:19:31, log line "priority normal" (the venv launcher pythonw stays BelowNormal; only the interpreter renders). Optional for Wes: re-run `tools/install_task.ps1` from his own terminal so the registration itself carries priority 5; the self-raise covers it until then. A fully saturated CPU still stalls a Normal process; acceptable for an ambient display, AboveNormal deliberately not used.
- [x] CPU clock: `sources/pdh.py` `CpuClock` (PDH `% Processor Performance` x `Processor Frequency`, 0.25 ms per collect, opened lazily on the poller thread, never raises); `SysPoller` reads it once a second (`ghz` = 0 without the counter); the CPU tile's bottom line is now `4.4 GHz`, the RAM line only as a fallback; zoom unchanged. Checked in `sim/board.png` (42 % / 4.4 GHz). 99 tests.
- [x] Housekeeping: merged branches `claude/sweet-mclean-8f59b9` and `claude/stoic-roentgen-a34d62` deleted. Allow rules moved on Wes's go at 21:24: the sixteen read-only PowerShell rules are in `~/.claude/settings.json` (46 allow entries, no duplicates, config repo committed); the project `.claude/settings.local.json` is an empty allow list.
- [x] `tools/preview.py` wrote the overlay zoom as `zoom-nowplaying|forecast.png`, invalid on Windows; the `|` is now `-`.
- [x] Task re-registered by Wes at 21:59 from his own terminal: priority 5 in the task XML, both pythonw processes Normal, startup line "priority normal".
- [x] Second stall, seen right after that: in board mode the task-launched app logged flushes of 100-140 ms 2-5 times a minute on an idle machine (CPU 3-4 %), never in ambient. Ruled out in turn: contention (idle); the USB link and the duty cycle (bench from a shell: 4.3 ms per key, bursts of 5 keys with 75-200 ms gaps 22 ms max, unchanged by a throttling opt-out); the build (a shell-launched copy of the same build in board mode: 0 slow ticks in 2 min 13 s, while every task-launched instance logged 2-5 a minute); memory priority (task 4, shell 5; raising it in-process changed nothing: 7 warnings in 2.5 min); the job object (flags 0x3000 = silent breakaway + kill on close, scheduling class 5, no CPU rate control); I/O priority (normal in both). Cause: Windows 11 power throttling (efficiency QoS) of a windowless Task Scheduler process, which makes an idle loop pay a wake-up penalty on each of the ~30 USB writes of a flush. Opting the live process out (`SetProcessInformation` ProcessPowerThrottling, execution speed + timer resolution, state 0) ended the stalls at once: 0 in 2 min. `main.normal_priority()` now does that at start, also normalizes memory priority, and the startup line logs class, memory priority, throttling and the job limits. 100 tests (the lock test no longer depends on the clock; it failed after 22:00).
- [x] Verified after the 22:19:44 restart with the opt-out baked in: 0 slow ticks in the first 2.5 minutes of board mode (22:19:44-22:22:16); the two task-launched instances before it logged 4 and 7 in the same span.
- [x] Overnight: `grep "slow tick" logs/deckdash.log` in the main checkout after a night of ambient rotation; expect none outside heavy load. Interim check 22:34 (no night yet): since the 22:19:44 restart the log holds 10 min of board mode without a slow-step warning, then `weather` at 13.9 fps with 0 slow ticks in both reported minutes (flush avg 41 ms, max 85 and 60 ms), ended by a key press at 22:31:48. Nothing more to compare until the morning. Result 2026-09-07 09:22: 497 ambient minutes from 23:08 to 09:22 with no restart, no lock and no sleep gap; 8 slow ticks in all (flush 101-165 ms, one frame each: plasma 4, tokyo 3, matrix 1; none in weather, life or aquarium), spread over the night at about one an hour, with nothing fetching or only the one-second sys poll; every minute held 13.9 fps at target 14 (life 4.0 at 4). Before the fix, board mode alone logged 2-5 a minute. The stall is closed; the residue is background noise at one frame an hour. Also overnight: the Bitaxe stopped answering for about 90 s at 03:22 and the weather API timed out once at 04:40, both recovered on their own.

## Phase 7 - deck-dash as an app
Spec agreed 2026-09-06 22:55 (Wes took all nine defaults, asked one at a time): the render loop stays the single long-running process and gains three faces, a `deckdash` command with a control subcommand, a tray icon, and a local dashboard page that opens as its own window. Lands after the overnight slow-tick grep and the README refresh. Acceptance for both halves: the slow-tick counter stays 0 in ambient mode with every face active.

### 7a: command, control channel, tray (one session)
Status: **done 2026-09-07 10:46** - code 2026-09-06 23:10, verified on the deck 09:35, reviewed and fixed 10:05, merged to `main`, installed editable, task re-registered by Wes at 10:46 (111 tests).
- [x] `pyproject.toml` (setuptools backend, name `deck-dash`, runtime dependencies = `requirements.txt` minus pytest, plus `pystray`): `[project.scripts] deckdash = "deckdash.main:main"` and `[project.gui-scripts] deckdashw = "deckdash.main:main"`. Editable install into the main-checkout venv (`pip install -e .`; pip fetches setuptools as the build backend, a one-time download). `python -m deckdash` keeps working. `install_task.ps1` runs `.venv\Scripts\deckdashw.exe` when it exists, else `pythonw -m deckdash`; its process filter now also matches the launcher's command line and skips `ctl` clients. Checked with `pip wheel`: `deck_dash-0.2.0` carries both entry points. The editable install must run from the main checkout after the merge (from a worktree it would point the venv at the worktree). pystray 0.19.5 is in the main venv.
- [x] `deckdash/control.py`: a daemon thread serves the Windows named pipe `\\.\pipe\deckdash` with stdlib `multiprocessing.connection` (`family="AF_PIPE"`, no port, same user only). One request tuple per connection, one reply dict (`ok`, `error`, payload). Commands go into `App.commands` (`queue.Queue`) and `_tick` drains it first (a "commands" mark). Commands: `status` (mode, scene, uptime, paused, locked, brightness, the last fps-line values, slow ticks since start, per-source last-fetch age and last error), `wake`, `scene NAME`, `next`, `pause`, `resume`, `brightness N|auto`, `toast KIND TITLE DETAIL` (a test toast through the normal toast path), `reload` (re-read config, rebuild slots, scene list, brightness and idle settings; sources untouched), `quit` (exit 0).
- [x] CLI: `deckdash ctl <command> [args]`; `argv[0] == "ctl"` dispatches to `deckdash/ctl.py` before argparse and prints the reply (`--json` for the raw dict). Two client-side commands: `ctl restart` runs `tools\install_task.ps1 -Stop` then `-Start` from the CLI process; `ctl open` opens the dashboard (7b; in 7a it prints the URL).
- [x] Pause (`pause` / `resume` from ctl, tray or dashboard): brightness 0, one tick per second, no rendering, sources keep polling, toasts wait as they do while locked; resume restores brightness and invalidates, so the board is current at once.
- [x] Second launch while the mutex is held (no `--sim`): opens the dashboard and exits 0. Verified on 2026-09-07 12:36 against the live task, which holds the mutex: the second copy logged "another deck-dash is already running; opening its dashboard", opened the window and exited 0, and the task's own minute line was unaffected (13.9 fps, 0 slow ticks). The check happens before the deck is opened, so a second launch never touches the hardware.
- [x] `deckdash/tray.py` (pystray, `icon.run_detached()` so the loop keeps the main thread; import-guarded, a missing package logs one line and the app runs without a tray). Icon: a 3x5 dot grid drawn with Pillow, green normally, grey while paused or locked, amber while a badge is pending. Menu: Wake board; Scene submenu (six scenes plus Next); Pause / Resume; Brightness (Auto, 100, 50, 30); Open dashboard; Open log (`os.startfile` on `logs\deckdash.log`); Edit config (`config.local.toml`, created empty if missing); Recalibrate gap (detached PowerShell: `install_task.ps1 -Stop`, `tools\calibrate.py`, `-Start`); Restart (detached PowerShell: `-Stop`, `-Start`); Quit. Left click = Open dashboard. Menu actions go through `App.submit` like every client. `[ui] tray = false` in `config.toml` turns it off; `--sim` runs never show one. Also: `wake` dismisses a toast without a badge, as a key press does.
- [x] Verified on the hardware 2026-09-07 09:24-09:35: the task stopped and a shell copy of this branch run from the worktree (`pythonw -m deckdash --ambient tokyo`). Startup logged `control pipe \\.\pipe\deckdash` and `tray icon up`; Wes confirmed the icon and used a menu item. Every `deckdash ctl` command answered `ok` (exit 0) and the deck followed: `status`, `scene plasma`, `pause` (deck off, logged `paused: deck off`), `resume`, `brightness 50` (`{"brightness": 50, "override": 50}`), `brightness auto` (back to 80, override null), `toast test hardware "7a check"` (logged `toast: TEST HARDWARE test (7a check)`), `wake`, `quit` (clean exit, both `pythonw` entries gone). Eight ambient minutes with the pipe thread and the tray running: **13.9-14.0 fps at target 14, flush avg 62-63 ms, max 66-74 ms, 14.6-14.8 keys per frame, 0 slow ticks** - unchanged against the Phase 6 baseline (66-70 ms), so the control channel and the tray cost nothing measurable. The run's only slow tick was the expected startup frame (128 ms, four sources fetching at once). Reading the log: `--ambient SCENE` returns to the scene 5 s after any press ([main.py](deckdash/main.py) help text), which is why each `ambient off` is followed by `ambient: tokyo` five seconds later; under the task the delay is `deck.idle_minutes` (10). The task was started again from the main checkout at 09:35:42 (v0.1.0, pre-7a, `priority normal, memory priority 5 (was 4), power throttling off`); the Claude-needs-you toast fired on it within a second, so the alert path came back with it.
- [x] Tests (`tests/test_control.py`, 107 in all): pipe round trip against a running `App` on `SimDeck`, every command's effect, pause semantics, reload rebuilding slots, tray icon rendering, `ctl` parsing and error paths, `main` dispatching `ctl`. Smoke on the simulator (`--sim` uses the pipe `deckdash-sim`, so it can run next to the live task): the pipe was up before the first status call, every command answered through the real `deckdash ctl`, pause held the loop at one tick per second, reload rebuilt the slots, exit 0. Note: without `config.local.toml` the worktree run hit `bitaxe.local` and got a 401; the live app on the LAN address is fine.

- [x] Adversarial review of the 7a diff before the merge (six dimension reviewers - concurrency, pipe protocol, app integration, tray, packaging, tests - then three refuters per finding through different lenses; 78 agents, majority-refuted findings dropped). Six survived, five distinct, all fixed, each with a regression test that was checked to fail against the pre-fix code (111 tests):
  1. `control.py` served the pipe on one thread and called `conn.recv()` with no deadline, and a `multiprocessing` pipe's default ACL lets any local user open it read-only. A peer that connected and never spoke parked the accept loop, so `ctl` and the 7b dashboard were dead until a restart while the tray kept working (it calls `App.submit` directly) - the app looked healthy. `conn.poll(RECV_TIMEOUT_S)` (2 s) now drops a silent client. Verified in the simulator: with a squatter holding the pipe, `ctl status` was still served, costing one 2.1 s delay instead of the channel.
  2. `App.reload` called `_apply_settings` before `build_slots`, which raises on an unknown tile name. A typo in `config.local.toml` therefore returned an error from `ctl reload` while brightness, the night window, `idle_minutes` and the scene list from the rejected file went live behind the old layout - and the periodic `_apply_brightness` pushed the rejected brightness to the deck within 30 s. The slots are now built into a local first, so a failed reload changes nothing.
  3. pystray's win32 backend snapshots the menu into a native HMENU and rebuilds it only on `update_menu()`, which ran only after a tray click. After `deckdash ctl pause` the icon went grey but the item still read "Pause", and clicking it resumed - so it looked like it did nothing. `Tray._sync` now compares a signature of (tray state, mode, paused, brightness override, scene names) once a second and pushes icon, tooltip and menu; a changed scene list rebuilds the menu object, so `reload` is reflected too.
  4. `calibrate_script` and `restart_script` were flat `;` chains, so Ctrl+C or a crashing calibration tool skipped the `-Start` after `-Stop` had already killed the app: deck frozen, no tray, no log, until `install_task.ps1 -Start` was run by hand. Both now put the `-Start` in a `finally`. Closing the console with the X still skips it (Windows kills the process outright), so the script prints the recovery command before it starts.
  5. `install_task.ps1 -Stop` matched a `--sim` run, which is meant to coexist with the live app on its own pipe; the filter gained `-notlike '*--sim*'`. Parse-checked, ASCII-clean, and evaluated against four synthetic `Win32_Process` rows: the live task and the `deckdashw` child match, the simulator and a `ctl` client do not.
- [x] `deckdashw.exe` process shape (checked before the re-registration gate, because `-Stop` has to keep working): the launcher spawns `pythonw.exe "...\deckdashw.exe" <args>` as a child, so the two `pythonw` entries stay and the `-Stop` filter matches them through its `*\deckdashw.exe*` pattern. Killing the child by PID showed the `.exe` stub exits with it rather than orphaning.
- [x] The fixes verified on the deck under the **task-launched** process, 2026-09-07 10:14 (the task restarted onto merged `main`; it still runs `pythonw -m deckdash` until Wes re-registers, but that is the same code). Startup logged `deckdash 0.2.0 ... priority normal, memory priority 5 (was 4), power throttling off`, `control pipe \\.\pipe\deckdash` and `tray icon up`; `deckdash ctl status` answered through the installed `deckdash.exe` from a neutral cwd. Fix 3 checked the only way it can be - by hand: `ctl pause` from the command line, then Wes right-clicked the tray and the item read **Resume** (it would have read "Pause" before), and clicking it brought the deck back (`resumed` at 10:27:02). The pause held 12 minutes at one tick per second with no slow ticks. Two tokyo minutes after the resume, task-launched with the pipe thread and the tray running: 14.0 and 13.9 fps at target 14, flush avg 61 ms max 69 and 67 ms, 0 slow ticks - the review fixes touch only the control and tray threads, and the render path is unchanged.

- [x] Task re-registered by Wes from his own terminal, 2026-09-07 10:46. The action is now `Execute: .venv\Scripts\deckdashw.exe` with no arguments, and the process tree is the predicted one: the `deckdashw.exe` stub (pid 14088) with the venv launcher and the real interpreter as `pythonw.exe` children carrying `"...\pythonw.exe" "...\deckdashw.exe"` on their command lines, which is what the `-Stop` filter matches. Startup at 10:46:14 logged `deckdash 0.2.0 (hardware), priority normal, memory priority 5 (was 4), power throttling off`, the control pipe and `tray icon up`. The ambient minutes either side were clean (plasma 13.9-14.0 fps, life 4.0 fps, flush max 64-68 ms, 0 slow ticks).

### 7b: dashboard page and window (one session)
Status: **done 2026-09-07 11:13** (121 tests; verified on the deck with the window open).
- [x] `deckdash/dashboard.py`: stdlib `ThreadingHTTPServer` on `127.0.0.1:8770` (`[ui] dashboard_port`) on a daemon thread, shaped like `ControlServer` (a `ready` event and an `error`, so a bound port is a warning and the deck still lights up). Routes: `/` serves `deckdash/ui/index.html`; `/api/status` = the `status` command plus `tray_state` and the page's own URL; `/api/frame.png` = the fifteen key images through `Canvas.compose` at the calibrated gap, PNG, cached 200 ms; `/api/cmd` POST `{cmd, args}` intercepts `restart` and `open` (client-side helpers that `control.validate` would reject as unknown) then validates and goes through `App.submit` like every other client; `/api/config` GET returns only the tunables the form owns, POST validates, writes and reloads. Three things the mapping turned up and the code has to do: `BaseHTTPRequestHandler.log_message` writes to `sys.stderr`, which is `None` under `pythonw`, so it is overridden (every request would otherwise raise in the handler thread); `protocol_version` is HTTP/1.1, or the 5 fps preview costs a TCP connect per frame; and `allow_reuse_address` is turned **off**, because on Windows it lets a second process bind a port another is already serving and the two would split the requests.
- [x] `/api/frame.png` needed a source of truth that did not exist: `DeckBase` kept a crc32 per key and dropped the pixels, and only `SimDeck` mirrored images. The mirror is now `DeckBase._shown`, filled in `flush` right after a successful send (so unchanged keys keep their last image, which is what the simulator already did) and re-sized on a reconnect. No copy is taken: a producer that mutated an image in place would already defeat the crc32 dirty check. `SimDeck` drops its own copy and composes the shared one.
- [x] Page panels, all in one file, dark, no CDN: live preview at 5 fps; status (mode, scene, brightness and whether it is fixed, lock, last press, ticks, slow ticks, badges, the last ambient minute's fps and flush, the deck's identity); every source with its age or its error; controls (scene buttons built from the registry, wake, next, pause/resume, brightness, test toast, restart, quit, both destructive ones behind a confirm); and the settings form - 15 tile selects laid out 5x3, an overlay editor, the scene rotation with tick boxes and up/down, scene minutes, ambient fps, idle minutes, day and night brightness with the night window, `slow_step_ms`, the news feeds and the CI repos.
- [x] Everything the form can post is validated in `dashboard.validate_settings` before it reaches the file, because `config.local.toml` is read at every start: a bad value outlives the process and would leave the deck dark at the next logon. Rejected: out-of-range numbers, NaN and infinity (both survive a TOML round trip), a night time that is not HH:MM (`_minutes` splits on ':' during the reload), unknown tile or scene names, a layout that is not 15 keys, control characters in any string (a newline in a feed name emitted a raw line break and made the file unparsable), a feed URL that is not http(s), a repo that is not owner/name. `config.write_local` now writes through a temp file and re-parses it before replacing the real one, so no caller can leave an unparsable config behind.
- [x] Switching an overlay off was impossible and fatal: `config.local.toml` merges into `config.toml` key by key, so an overlay set in the tracked file cannot be removed from the local one, and writing an empty value raised `KeyError` in `build_slots` and then broke the next startup too. An empty overlay value now means "no overlay", which makes blanking one the supported way off; the page posts every known overlay, blanked ones as empty.
- [x] `restart_required` compares the news feeds and the CI repos before and after, because `NewsPoller` and `GhPoller` read their lists in `__init__` and `App.reload` deliberately leaves the sources running. Everything else on the form takes effect on the reload.
- [x] Restart from the page reuses `control.restart_script` and `run_powershell`, the same detached PowerShell `ctl restart` uses, so the `-Start` in its `finally` still applies.
- [x] Window: `control.open_window` was already written and is used unchanged by the tray's Open dashboard, by `ctl open` (the Phase 7a stub is gone) and by a second launch, which now opens the window and exits 0 instead of logging an error and exiting 3 - launching deck-dash again is how you ask for its window. The simulator takes the next port up, as it takes its own pipe, so it can run beside the live copy. `[ui] dashboard_port` added to `config.toml`; `deckdash/ui/*.html` added to the packaging config, since the editable install finds it but a wheel would not.
- [x] Acceptance on the deck 2026-09-07 11:11-11:13, tokyo with the Edge window open and the preview streaming (four established connections): **13.9 fps at target 14, flush avg 61-62 ms, max 66 and 69 ms, 0 slow ticks** in both minutes - inside the Phase 6 baseline of 66-70 ms. Wes confirmed the window and the live preview. An earlier run showed the one cost worth knowing about: the first `/api/frame.png` loads PIL's PNG writer on the request thread and cost the render loop four slow ticks just as the page opened, with no source fetching to blame. The server now encodes one frame at start, before it serves anything, and the same cold-start test produced none.
- [x] Tests (`tests/test_dashboard.py`, 121 in all): every endpoint against a running `App` on a `SimDeck` with the loop driven from the test thread, the PNG's exact composed size, the 200 ms cache, a key that was never written, JSON numbers reaching `brightness` as numbers rather than strings, unknown and malformed commands, `open` being intercepted before validation, a busy port degrading to `error` instead of throwing, a save that writes and reloads and reports `restart_required`, nine rejected saves that must never touch the file, `write_local` refusing an unparsable write, a blanked overlay building a plain tile, and `effective()` never echoing the weather coordinates, the Bitaxe address or the VPS host.
- [x] README: a "Using it" section covering the tray, the dashboard and every `ctl` command, plus the editable install as step 6 of Setup (the old "a second copy exits immediately" line is no longer true).
- [x] Adversarial review of the 7b diff after the push (seven dimension reviewers, three refuters each, 91 agents): 15 findings survived, collapsing to four distinct problems, all fixed with a regression test each that was checked to fail against the pre-fix code (125 tests).
  1. **Cross-site request forgery, found independently by all seven dimensions and reproduced end to end by several refuters.** `_body` parsed the request body with `json.loads` whatever its Content-Type was, and nothing looked at `Origin`. `text/plain`, `application/x-www-form-urlencoded` and `multipart/form-data` are CORS-safelisted, so a plain `<form>` on any site posts to `127.0.0.1:8770` with no preflight, and the side effect lands even though the reply is unreadable to the attacker. The classic trick shapes the body into valid JSON by putting the form's `=` inside an ignored key: `<input name='{"cmd":"quit","args":[],"x":"' value='"}'>`. Any page Wes visited could therefore run `quit` (deck dark until the task restarts it), `restart` (a detached PowerShell over the scheduled task), `open` (an Edge window per request), or POST `/api/config` to write `brightness = 0` into `config.local.toml`, which is read at every start and so survives every restart. Binding to loopback is no defence: the browser *is* a local process. POSTs now require `Content-Type: application/json` - which a cross-origin caller cannot send without a preflight we never answer - and reject a foreign `Origin`. The page itself is same-origin, so it needs no preflight and is unaffected.
  2. **DNS rebinding**, also found by five dimensions. Nothing validated the `Host` header, so the server answered any name that resolved to loopback. A page on an attacker domain whose record is re-pointed at 127.0.0.1 is *same-origin* with the dashboard as far as the browser is concerned, which makes the replies **readable** - `/api/frame.png` (a live picture of the deck), `/api/status` (whose per-source error strings carry the hosts from `config.local.toml`) and `/api/config`. Requests are now refused unless `Host` is loopback, on GET as well as POST.
  3. **The hardened write introduced its own leak.** `write_local`'s temp file is a complete copy of `config.local.toml` - coordinates, LAN address, VPS hosts - written as `config.local.tmp.toml` in the repo root, which `.gitignore` did not cover, and it was only removed on the unparsable-TOML path. `os.replace` raises `PermissionError` on Windows whenever anything holds the destination open, which the tray's Edit config does, so an ordinary save while the file was open in an editor would strand that copy in a public repo. It is now removed in a `finally` on every path, and `config.local.*.toml` is gitignored.
  4. **The tests could drive each other.** `serve()` waited on `ready`, which `Dashboard.run` sets on the refused path too, and never checked `error`; with hard-coded ports, two overlapping runs meant the loser silently made requests against the winner's server and mutated its `App` (reproduced by running the file twice at once). Tests now bind port 0 and let the OS choose, and `serve()` asserts the bind succeeded.
  Not treated as findings, deliberately: that there is no authentication at all (loopback-only on a single-user desktop is the recorded design), and that `/api/config` writes are unauthenticated beyond the origin check.

- [x] Verified under the **task-launched** process 2026-09-07 11:34, which is the only place one hazard shows up: the task runs without a console, so `sys.stderr` is `None`, and `BaseHTTPRequestHandler.log_message` writes straight to it - unoverridden, every request would have raised inside the handler thread and the page would have looked dead while the app looked healthy. Twenty alternating `/api/frame.png` and `/api/status` requests against the live task process: 20 ok, 0 failed, no handler warnings in the log, the page served at 20,293 bytes, and `/api/config` carried no LAN address, no `ssh_host` and no coordinates. The task's action is `.venv\Scripts\deckdashw.exe`, and its startup logged the control pipe, `dashboard http://127.0.0.1:8770/` and `tray icon up`.
Decided against, reasons recorded: PyInstaller exe (Defender false positives, size, DLL bundling; the venv plus the logon task already behaves like an installed app), tkinter (dated, a third surface to maintain), Qt / Electron / Tauri (heavy or need MSVC or Rust, neither installed), pywebview (needs pythonnet; Edge app mode gives the window for free), LAN access (localhost only; revisit with a token if the phone case comes up).

Gate passed 2026-09-06 22:45 ("execute in that order"): pystray and the setuptools build fetch approved with the go.

## Merge and push
- [x] Evening of 2026-09-06: `main` fast-forwarded to `406ba2e`, a merge of `claude/sweet-mclean-8f59b9` (Phases 2-4) that also folds in the Phase 1 hardware-review notes `main` had picked up meanwhile (a plain fast-forward was impossible because of that one commit). Local only: `main` is six commits ahead of `origin/main`. 92 tests pass on the merged tree. The main checkout has the code, the venv, and `config.local.toml`, so the scheduled task can run from it.
- [x] Pushed 2026-09-06 20:25 on "push it": `origin/main` now at `0ca19df` (96787c3..0ca19df, twelve commits). Both worktrees unregistered with `git worktree remove --force` + `git worktree prune`; `git worktree list` shows only `main` and the current session worktree. The two empty folders `sweet-mclean-8f59b9` and `stoic-roentgen-a34d62` stay on disk ("being used by another process") until the desktop-app tabs whose cwd they were are closed; then `rmdir` them. The merged branches `claude/sweet-mclean-8f59b9` and `claude/stoic-roentgen-a34d62` still exist locally (`git branch -d` when convenient).


## Sleep/wake: the reconnect path, tested at last
Status: **passed 2026-09-07 12:57** on the real deck, the last untested path in the project.
Wes slept the PC from an ambient scene, left it about seven minutes, and woke it. The point of the
test is that the process must SURVIVE: a shutdown would only re-run `open_with_retry`, which the
logon task exercises daily, whereas a suspend makes the running app lose the device and recover it
in place. Pid 18356 before and after, so the in-process path is what ran.
- 12:50:49 `deck write failed, will reconnect: Failed to write out report (-1)` - the transport error
  caught in `RealDeck._send`; one slow tick (flush 1042 ms) for the failing write.
- 12:50:53-12:57:56 asleep. NVML and the Bitaxe went unreachable too.
- 12:57:59 `deck open:` reporting the same serial as before the suspend - `_maybe_reconnect` caught it about three
  seconds after resume, inside its 5 s retry. One slow tick (flush 2494 ms) for the reconnect plus the
  full repaint that `invalidate()` forces. 12:58:01 the scene resumed by itself.
- Everything downstream came back too: `MINER OFFLINE` on the way down and `MINER BACK 1.06T` thirteen
  seconds after resume; the wake's lock screen darkened the deck at 12:58:31 and restored it at
  12:58:41; key presses registered and zoomed; and every source reads ok, including NVML, which had
  failed five times during the suspend and was the one most likely to stay wedged.
- Cost: exactly two slow ticks, one at each boundary. That is the floor for a suspend and a resume.
- Aside, for the BSOD tracker: the machine slept and woke cleanly, which is a data point in the
  observation window after the BIOS flash and the XMP step-down of 2026-09-05.

## After Phase 7: three fixes for a second deck
Offered at the end of Phase 7, approved 2026-09-07 13:20 ("all three, one commit each"). Wes's wife
has the same 15-key deck and wants the same setup from the public repo, which is what these are for.
Three commits plus a PHASES pair, pushed to `origin/main` 2026-09-07 14:12 (7954437..b54fed8, a fast-forward). The quit fix below came after that push and is not on `origin/main` yet.

- [x] `tools\install_task.ps1` read `deck.hidapi_dir` from `config.toml` only, so the repo's own
  "machine-local goes in config.local.toml" convention broke task registration on any machine whose
  DLL is somewhere else. A `Get-HidapiDir` helper now reads one file at a time; the caller tries
  config.local.toml and then config.toml, and both the preflight line and the not-found message name
  the file the path came from. Two smaller bugs went with it: two matching lines made `$hidDir` an
  `Object[]`, and `Join-Path` then threw "Cannot find drive" once per element (reproduced against the
  old two-liner), and a value in TOML's single quotes passed the regex through unchanged as the whole
  line. Dry-run against seven synthetic config pairs - tracked only, local overriding, local without
  the key, single quotes, duplicate lines, trailing comment, neither - with the function *and* the
  fallback chain extracted from the script rather than retyped: all seven pass. Parse-checked with
  `ParseFile`, ASCII-clean, CRLF intact.
  Exercised for real 2026-09-07 14:47, which until then it never had been: Wes added
  `hidapi_dir` to the `[deck]` table of `config.local.toml`, checked the file still parsed with
  `deckdash ctl reload` before anything depended on it (an unparsable local config is read at every
  start and would leave the deck dark at the next logon with the task retrying every minute), and
  re-registered from his own terminal. The preflight line read **`hidapi.dll : 166912 bytes at
  C:\...\hidapi\hidapi.dll (from config.local.toml)`** - `(from config.toml)` before. The task
  re-registered, the old copy was stopped and the deck came back at 14:47:14 on the same serial,
  mode board, 0 slow ticks. The tracked `config.toml` was left alone, so the repo still pulls clean
  and its path is only the fallback.
- [x] `main()` opened the deck before it built the `App` and started the three faces, and
  `open_with_retry` waits for ever by design (at logon the USB stack may still be waking up), so a
  missing hidapi.dll or a deck another process already held gave no tray, no dashboard, no pipe and
  no crash: the app was alive and completely silent, with only the log to say why. The faces come up
  first now and the open moved inside the `try`, so a failure there still tears all three down. `App`
  is built against `DeckBase.key_count`'s default of 15 and the slots are rebuilt once if the deck
  that finally answers reports a different count, which the old order got implicitly. Made *visible*
  rather than merely non-silent: `App.mode` reads "waiting" and `tray_state()` "off" (grey) until the
  deck opens - `opened` is cleared only by `close()`, never by a transport error, so neither reads
  waiting during the in-place reconnect after a suspend; `status()["deck"]` carries `open`; the page's
  deck row says "waiting for the deck" in the warning colour instead of a deck identity that is not
  there yet; and `submit()` answers `status` inline while the loop is not turning, because nothing
  drains the command queue yet and every client would otherwise have sat through the 3 s timeout for
  the one command that tells it what is wrong, while anything that would drive the board is refused
  with the reason. 127 tests: a unit test of the four not-yet-open behaviours, and an end-to-end test
  that runs `main()` against a deck that blocks in `open()` and asserts the pipe, `/api/status` and
  the page all answer while it is blocked, then that the order was tray, deck, run. Both were checked
  to fail against the pre-fix code (the ordering test fails fast on `send`, it does not hang).
- [x] README "A second machine", eight steps in the order that avoids the traps: Original / V2 /
  MK.2 are drop-in fifteen-key units; the **x64** DLL out of `hidapi-win.zip`, because the x86 copy
  loads without complaint and then finds no deck, which reads like a missing device; the Elgato
  software quit and unset from launch-at-startup *before* the deck is plugged in; everything
  machine-local in `config.local.toml` starting with `hidapi_dir`; `pip install -e .` and never a
  plain `pip install .`; and the three sources that need something outside the PC - a miner on the
  LAN, the `gh` CLI logged in, an SSH alias - each switched off by the same empty value
  (`host = ""`, `repos = []`, `services = []`), which stops the poller and not just the tile. Also
  corrected: the tray's default menu item opens on a left click, not a double-click.
- [x] **Quit had to be let out of the wait, found while setting up that hardware check.** The tray's
  Quit is wired through `App.submit` like every other menu item, so the refusal above covered it:
  the icon was up, the wait was endless, and the only way to be rid of it was Task Manager or
  `install_task.ps1 -Stop`. That is a worse failure than the silence it replaced. `submit` now
  answers `quit` as well as `status` while the deck is not open, `open_with_retry` takes a stop
  predicate and slices its 10 s sleep into 0.2 s so the flag is seen promptly, and `main` returns 0
  through the same `finally` that stops the three faces. 128 tests, the new one checked to fail
  against the pre-fix code; its `main()` thread is a daemon so a future break in the quit path fails
  the test instead of wedging pytest at exit.
- [x] **Seen on the hardware, 2026-09-07 14:14-14:15**, the deck dark for 50 s. The task stopped,
  then this branch launched from the worktree with `--hidapi` pointing at an empty folder (no
  `config.local.toml` was touched: the flag overrides the config, and `--config` pointed at a copy
  with the weather coordinates pinned so nothing was written). The log is the whole point:

      14:14:42,945 control pipe \\.\pipe\deckdash
      14:14:42,950 dashboard http://127.0.0.1:8770/
      14:14:42,966 tray icon up
      14:14:43,055 WARNING deck not available (attempt 1): Probe failed to find any functional
                   HID backend ... Is the 'hidapi.dll' library installed?

  Before the fix that warning was the only line, and there was nothing else to ask. All three faces
  answered while the wait ran: `ctl status` gave `mode waiting`, `ticks 0`; `/api/status` gave
  `mode waiting`, `tray_state off`, `deck {'type': 'RealDeck', 'open': False}`; the page served
  20,386 bytes and `/api/frame.png` 1,421 (a dark board); `ctl scene tokyo` was refused with "the
  deck is not open yet, so 'scene' has nothing to drive", exit 1. Then `ctl quit` answered `ok` and
  the process was gone 116 ms later (`quit requested` 14:15:10,004, `quit while waiting for the
  deck` 14:15:10,120), leaving no python process and no tray icon. `install_task.ps1 -Start` brought
  the deck back at 14:15:22 on the same serial, mode board, 0 slow ticks. Not confirmed
  visually: that the tray icon was actually grey rather than green - the API said `tray_state off`,
  which is the value the icon is drawn from, but nobody looked at it.

- [x] **Live under the task since 2026-09-07 14:24:48**, on "push it": `origin/main` at c1c4b75, the
  main checkout pulled (fast-forward, clean), `-Stop` then `-Start`. The new order is visible in the
  live log, which is the whole change in one place - `starting`, `control pipe`, `dashboard`,
  `tray icon up`, then `deck open:` 80 ms later; the 14:15:22 restart an hour earlier, on the old
  code, logged `deck open:` first. No re-registration was needed: the task's action is still
  `.venv\Scripts\deckdashw.exe` and the editable install points at the checkout. Two tokyo minutes
  with all three faces up, task-launched: **13.9 fps at target 14, flush avg 62 ms, max 68 and
  65 ms, 13.9-14.0 keys per frame, 0 slow ticks** in both - inside the Phase 6 baseline of 66-70 ms
  and level with the 7b measurement of 61-62 avg / 66-69 max. Zero slow-tick warnings and no
  warning or error of any kind since the restart, so starting the faces before the deck costs the
  render loop nothing measurable, as expected: it only moves work that used to happen after the
  open to before it.

- [x] **`[gpu] enabled`, the off switch the one remaining source did not have** (2026-09-07 15:05,
  found while writing the prompt for the second machine). The miner, the CI list and the VPS each
  switch off by having nothing configured - `host = ""`, `repos = []`, `services = []` - and that
  stops the poller, not just the tile. The GPU had no equivalent: NVML is the only way into an
  NVIDIA card and `nvmlInit()` raises without the driver, so on a machine with no NVIDIA GPU the
  poller failed once a second for ever and the dashboard showed it red, with nothing to be done
  about it; blanking the layout key removed the tile and left the failing poller behind. That made
  the README's "three sources need something outside the PC" wrong, since it is four. `GpuPoller`
  now reads `[gpu] enabled` (default true, so nothing changes on a machine with a card) and, when
  false, never starts its thread and reports "disabled". Also fixed alongside: a source that is
  switched off is not one that is failing, and both `ctl status` and the dashboard rendered
  `failing x0: disabled` - which is how the now-playing helper has looked all along whenever it was
  turned off. A zero-failure error now reads as a plain muted note in both. 129 tests, both new
  assertions checked to fail against the pre-fix code. Verified on the hardware at 15:07 by
  restarting the task onto the new build: all eleven sources ok, gpu included, no warning of any
  kind - the default-on path is unchanged on a machine that has a card, which is the only thing
  this change could have broken here.

Worth knowing: `pytest` on this machine prints "Windows fatal exception: access violation" with a
thread dump on roughly two runs in three, and the suite still reports all green. It predates these
changes - it reproduces with the 125 pre-existing tests and the new ones deselected - so a future
session should not read it as a regression from them.

## Ambient weather: ground under the sky (0.3.0)

Asked for on 2026-09-07 15:40 - "a more detailed background that has ground in the field of view",
because with a clear sky the scene was a bare gradient and the whole bottom row was empty. Merged as
PR #1 (`ab68a6b`), tagged **v0.3.0**, the repo's first tag, and live under the task from 19:07:55.

- [x] **The land is silhouette masks, recoloured, not a second palette.** Two ridges receding into
  haze, the hills, and the field, each a `L` mask built once in `_build_land` and pasted per frame
  through a colour lerped from the ground colour toward `_haze(sky_bottom)` by how far away that band
  is. So the landscape is lit by whatever the sky is doing - dawn, noon, dusk, storm - and there is no
  second set of colours to keep in step with sunrise and sunset. `_haze` deliberately pulls a third of
  the saturation out of the sky before hazing toward it: lerping at the raw sky turns the hills orange
  at sunset, which reads as mud rather than as distance.
- [x] **Trees and buildings get a mask of their own.** The treeline, a lone tree and a barn with a
  silo first went into the band they stand on, which meant they were the same colour as it: they only
  showed as a scalloped edge against the band behind, and under snow they vanished into white ground.
  Painted darker than any band, they read in every condition. The same change let the snow caps drop
  off the props - a 3 px white edge tracing every conifer read as line art - and stay on the smooth
  terrain crests, where it reads as snow.
- [x] **Geometry is placed off the key rows, not off the canvas.** The 24 px of bezel between the
  middle and bottom rows is invisible, and the first pass put `horizon` at `0.68 * h`, which left the
  entire treeline inside it: the trees were drawn, and simply could not be seen. The same trap
  horizontally put the lone tree at `0.17 * w`, which is the gap between keys 10 and 11. The bands are
  now derived from `KEY + self.gap`, ridges in the lower half of the middle row and everything with a
  shape to it inside the bottom row, and landmarks sit on key centres.
  `test_weather_scene_landmarks_avoid_the_bezel` asserts no prop ink falls in the horizontal bezel
  band, that every bottom-row key has land detail, and that the barn window is inside one key.
- [x] Sun and moon rise and set behind the ridges on a real arc whose ends sit under the horizon;
  the moon is drawn at tonight's actual phase from the synodic month. Snow settles on the crests,
  rain throws splashes on the field, fog lies on the land instead of hanging in mid-sky. A clear sky,
  the case that prompted all this, also gets cirrus wisps, gliding birds and a rare shooting star.
- [x] **Cost, and the one number that moved.** 4.1-5.5 ms per frame in the simulator against a 71 ms
  budget at 14 fps, land build ~3 ms once per showing. On the deck, two clean minutes hand-run:
  **13.9 fps at target 14, flush avg 42-43 ms, max 61 ms in the steady minute (99 ms in the first,
  a startup outlier), 9.8-10.0 keys per frame, 0 slow ticks.** Before the rewrite the same scene ran
  **flush avg 15-21 ms at 3.4-5.0 keys per frame**. The cause is the swaying grass: the blades are
  spread across the full width, so all five bottom-row keys are dirty every frame where the bottom row
  used to be static sky. Inside budget, with materially less headroom - if a future scene needs it
  back, halve the sway rate or the blade count before touching anything else.
- [x] Reviewed on the hardware by Wes, 2026-09-07 ("I like it"). The device was freed with
  `install_task.ps1 -Stop` and a hand-run `pythonw -m deckdash --ambient weather` took it; note that
  `-Start` alone does not recover from that, because the hand-run copy still holds the named mutex and
  the task's process exits with "another deck-dash is already running" - it takes `-Stop` then
  `-Start`.

## Releases

`CHANGELOG.md`, added with the above, gives deck-dash the scheme fpv-sim and fpv-sim-mcp already use,
since it had none: [Keep a Changelog](https://keepachangelog.com/) plus semver, the version living in
`deckdash/__init__.py` and read from there by `pyproject.toml`, the tag `vX.Y.Z` on the **merge
commit**, and the GitHub release notes taken verbatim from that version's section. Before 1.0.0 the
minor number carries features and the patch number carries fixes.

`0.1.0` and `0.2.0` are reconstructed entries, written from the commits and from this file rather
than from anything contemporaneous - the boundary itself is real (`f318eca`, Phase 7a, is the commit
that bumped the version), but a reader should trust the commits over my summary of them. `0.3.0` is
the work above and is the first release that was written as part of the change.

Status at hand-off, 2026-09-07 19:10: **everything through Phase 7, the five fixes after it, and the 0.3.0 weather rewrite are done, verified and live.** Phase 7 (the `deckdash` command, the named-pipe control channel, the tray, and the localhost dashboard in its Edge app-mode window) was verified on the deck, reviewed adversarially twice, and the sleep/wake reconnect path with it. Then five fixes: `install_task.ps1` honours `hidapi_dir` from `config.local.toml`; `main()` starts the tray, the dashboard and the pipe before it waits for the deck, so a deck that never arrives is visible instead of silent, and `quit` reaches that wait; the README gained an "A second machine" section; the tracked `hidapi_dir` default became the generic `C:/Tools/hidapi`; and `[gpu] enabled` gave the last source without one an off switch. Each was exercised on the hardware, including the two that only show up there: the waiting state (deck deliberately unavailable, 50 s dark) and the `hidapi_dir` lookup reading `(from config.local.toml)` for the first time. Since then the ambient `weather` scene was rebuilt with land under the sky, reviewed on the deck by Wes, merged as PR #1 and tagged **v0.3.0** - the repo's first tag, and the point at which `CHANGELOG.md` gave the project a release scheme. 130 tests pass. `origin/main` is at ab68a6b, the main checkout is level with it, and the task was restarted onto that build at 19:07:55 (`deckdash 0.3.0 starting`), which is the copy holding the deck now.

What is left is one thing, and it is first use rather than a defect: the README's second-machine section is unproven until someone walks it on the second PC. A prompt for that session was written on 2026-09-07 and covers the traps the README does not spell out - the Elgato software owning the device, the x64-versus-x86 DLL, `pip install -e .`, the task registration needing a non-sandboxed shell, and `ctl reload` before anything depends on a hand-edited `config.local.toml`. Also unconfirmed, deliberately: that the waiting tray icon is visibly grey - the API reported the value the icon is drawn from, but nobody looked at it.

Worth knowing for whatever comes next: `config.write_local` cannot delete a key from a table (it merges), which is why a blank overlay value means "no overlay"; it writes through a temp file and re-parses before replacing, because `config.local.toml` is read at every start and one unparsable write would leave the deck dark at the next logon; `RealDeck.opened` means "has been opened", not "the handle is live" - a transport error leaves it true, which is what lets the waiting state stay quiet through a suspend; and a lone CR anywhere in a file stops git normalizing it, so a patch script that inserts CRLF and then converts LF to CRLF over its own output will silently flip a whole file's line endings in the blob. Housekeeping is done: the idle `peaceful-bose-9f4101` session archived, its worktree and branch removed, leaving only `main` and the session worktree this was written from - and that one goes when its session is archived.
