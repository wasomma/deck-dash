# Changelog

All notable changes to deck-dash are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0 the minor number carries
features and the patch number carries fixes. A release is tagged `vX.Y.Z` on the merge commit, and
the GitHub release notes are that version's section of this file.

The version itself lives in `deckdash/__init__.py`; `pyproject.toml` reads it from there.

## [Unreleased]

### Added

- A **`usage` tile** for Claude Code: three bars on one key — the context window of the newest
  session, the 5-hour limit and the weekly all-models limit — each coloured green through amber
  to red, with the worst of the three as a heading. Zoom gives each meter a full row with exact
  token counts, the model, and a reset countdown.
- The context window is read from the **session transcript** (`~/.claude/projects/*/*.jsonl`),
  which every session writes, Desktop included. Only the last assistant record is parsed, from a
  256 KB tail read, and only when the file's mtime changes - 0.02 ms per poll at rest, 1.4 ms
  when a turn lands. `[claude_usage] context_window` supplies the denominator, since the
  transcript records token counts but not the window size; the zoom shows the raw count too.
- The 5-hour and weekly numbers come from Claude Code's **statusLine** command rather than hooks:
  `tools/claude_status.py` caches the payload it is handed into `state/claude-usage.json` and
  echoes a compact line back, so the terminal status line reads the same three numbers. Setup is
  in `docs/claude-hooks.md`; a tile with no source at all reads `no sessions`, never a false 0%,
  and a snapshot older than `[claude_usage] stale_minutes` greys the bars and shows its age.

  **The Desktop app does not run the statusLine command.** Measured 2026-09-07: a fresh Desktop
  session fired its SessionStart and UserPromptSubmit hooks into `state/claude-events.jsonl` and
  never touched `state/claude-usage.json` - same settings file, same `python "..."` command form,
  trusted workspace, `disableAllHooks` unset. The status line is a terminal element and Desktop
  renders its own UI, so it has none to fill. On a Desktop-only machine the transcript is the
  only context source and the limit bars show `-` until an authenticated source exists for them.

- A toast when the 5-hour or weekly limit crosses `[claude_usage] alert_pct` (90 by default),
  and another when it drops back under.

  There is no weekly Fable bar. Claude Code tracks only `five_hour`, `seven_day`,
  `seven_day_overage_included` and `overage` from its response headers, and the statusLine
  payload narrows that further; the per-model weekly lives only in the `/api/oauth/usage` API,
  which needs an OAuth token deck-dash does not hold. The zoom view labels the slot `Fable: n/a`
  rather than leaving a silent gap.

### Fixed

- **Restarting deck-dash from the app did nothing, silently.** `run_powershell` started the
  child with `DETACHED_PROCESS`, which leaves PowerShell without a console: it exits 0 without
  running its `-Command` at all. The tray's Restart, the dashboard's Restart and
  `deckdash ctl restart` all reported a pid and left the old process running. It now uses
  `CREATE_NO_WINDOW`, which still hides the window; survival never depended on the flag, since
  the task's job carries silent-breakaway (limit flags 0x3000) and children leave it on their own.
- The same helper sent the child's stdout and stderr to `DEVNULL`, which is what made the
  failure invisible. It now appends them to `logs/powershell.log` with the script and a
  timestamp, so a restart that throws leaves a trace.

### Changed

- **`bsod` now shares the `vps` key** as an overlay instead of holding key 10 of its own, which
  is what made room for `usage`. It takes the key for `[bsod] overlay_hours` (48) after a crash
  and shows the VPS the rest of the time — a bugcheck is rare and important, which is exactly
  what the overlay mechanism is for.

## [0.3.0] - 2026-09-07

### Changed

- The **ambient weather scene** is a view out of a window rather than a bare sky. Under the clouds
  there is now land: two ridges receding into haze, hills, and a field with a fence, grass and
  scattered clumps. A treeline, a lone tree and a barn with a silo stand on top of them. Each piece
  is a silhouette mask built once when the scene starts and recoloured every frame, hazed toward
  the current sky colour by how far away it is, so the landscape is lit by whatever the sky is
  doing and there is no second palette to keep in step with sunrise and sunset.
- The sun and the moon rise and set behind the ridges on a real arc, and the moon is drawn at
  tonight's actual phase.
- Snow settles along the terrain crests, rain throws splashes on the field, and fog banks lie on
  the land instead of hanging in mid-sky.
- A clear sky, the case that looked plainest, also gets cirrus wisps, gliding birds, and an
  occasional shooting star after dark.

### Fixed

- Landmarks are placed off the key rows rather than off the canvas. The 24 px of bezel between the
  middle and bottom rows is invisible, and a first pass left the whole treeline inside it; a thin
  shape stranded in a gap simply disappears. `test_weather_scene_landmarks_avoid_the_bezel` fails
  if it happens again.

### Performance

- The scene costs 4.1-5.5 ms per frame in the simulator, against a 71 ms budget at 14 fps. On the
  hardware it holds 13.9 fps with no slow ticks, but the moving grass makes all five bottom-row
  keys dirty every frame: flush is 42 ms average and 61 ms worst, against 15-21 ms before, at
  9.8 keys per frame against 3.4-5.0. Comfortable, with less headroom than it had.

## [0.2.0] - 2026-09-06

### Added

- `deckdash` as an application: a console entry point, a named-pipe control channel with the
  `deckdash ctl` CLI, and a tray icon for wake, scene, pause, brightness, log, config, recalibrate,
  restart and quit.
- A dashboard on 127.0.0.1 only, with a live deck preview and a window to open it in.
- A `[gpu] enabled` switch, the one source that had no way to turn it off.

### Fixed

- Quit works while waiting for a deck that never arrives.
- The tray, the dashboard and the pipe start before the deck is opened, so the app is reachable
  when no deck is attached.
- `install_task.ps1` reads `hidapi_dir` from `config.local.toml` first, and `hidapi_dir` has a
  generic default rather than one machine's path.
- The dashboard refuses cross-site posts and rebound hosts.

## [0.1.0] - 2026-09-06

### Added

- The core loop: a virtual full-deck canvas sliced into keys, dirty-key hashing, a per-tick byte
  budget with round-robin carry-over, reconnect on write failure, and a simulator that needs no
  hardware.
- Tiles for clock, weather, a five-hour forecast, GPU, CPU, network, Bitaxe, CI, VPS health, BSOD
  watch and a news marquee, each with a full-deck zoom view.
- Ambient scenes shown after the idle timeout: `weather`, `plasma`, `life`, `matrix`, `aquarium`
  and `tokyo`.
- Alerts, night brightness, a dark deck while the session is locked, and an at-logon Task Scheduler
  entry installed by `tools/install_task.ps1`.

[0.3.0]: https://github.com/wasomma/deck-dash/releases/tag/v0.3.0
[0.2.0]: https://github.com/wasomma/deck-dash/releases/tag/v0.2.0
[0.1.0]: https://github.com/wasomma/deck-dash/releases/tag/v0.1.0
