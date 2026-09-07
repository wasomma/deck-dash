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
- [ ] Wes eyeballs the scenes on the hardware (idle 10 min, or `.venv\Scripts\python -m deckdash --ambient aquarium`).

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
- [ ] Wes eyeballs it on the hardware (`--ambient tokyo`).
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
- [ ] Next session: `grep "slow tick" logs/deckdash.log` in the main checkout after a night of ambient rotation; expect none outside heavy load.

## Merge and push
- [x] Evening of 2026-09-06: `main` fast-forwarded to `406ba2e`, a merge of `claude/sweet-mclean-8f59b9` (Phases 2-4) that also folds in the Phase 1 hardware-review notes `main` had picked up meanwhile (a plain fast-forward was impossible because of that one commit). Local only: `main` is six commits ahead of `origin/main`. 92 tests pass on the merged tree. The main checkout has the code, the venv, and `config.local.toml`, so the scheduled task can run from it.
- [x] Pushed 2026-09-06 20:25 on "push it": `origin/main` now at `0ca19df` (96787c3..0ca19df, twelve commits). Both worktrees unregistered with `git worktree remove --force` + `git worktree prune`; `git worktree list` shows only `main` and the current session worktree. The two empty folders `sweet-mclean-8f59b9` and `stoic-roentgen-a34d62` stay on disk ("being used by another process") until the desktop-app tabs whose cwd they were are closed; then `rmdir` them. The merged branches `claude/sweet-mclean-8f59b9` and `claude/stoic-roentgen-a34d62` still exist locally (`git branch -d` when convenient).

Status at hand-off, 2026-09-06 22:23: Phase 6 is code-complete and live, including the board-mode stall fix (power throttling opt-out). `main` = this worktree branch (fast-forwarded), ahead of `origin/main` by the Phase 6 commits; push pending on "push it". The scheduled task (re-registered by Wes at priority 5) runs from main with the slow-step log; 100 tests pass. Open: a next-session grep of the slow-tick log after a night of rotation. This session's worktree `exciting-lehmann-b57392` is merged into main and can be archived from the sidebar when done.
