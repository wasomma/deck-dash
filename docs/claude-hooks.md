# Claude tiles: hook and statusLine setup

The `claude` tile shows one dot per live Claude Code session and pulses amber while a session
waits for input (a permission prompt or an idle prompt). It takes over the `net` key while a
session is working, waiting, or finished within the last 15 minutes, and a toast fires the
moment a session needs attention. Pressing the tile acknowledges the waiting sessions.

It is fed by hooks: every hook call appends one JSON line to `state/claude-events.jsonl`
(gitignored) through `tools/claude_hook.py`, which is standard library only and always exits 0.

## Hooks to add to `~/.claude/settings.json`

Merge this into the existing `hooks` object (the update-config skill does the merge). The
command uses forward slashes so it works from both cmd and Git Bash.

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "python \"C:/Users/WesF/Desktop/Dev/Projects/deck-dash/tools/claude_hook.py\"", "timeout": 5 } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python \"C:/Users/WesF/Desktop/Dev/Projects/deck-dash/tools/claude_hook.py\"", "timeout": 5 } ] }
    ],
    "Notification": [
      { "hooks": [ { "type": "command", "command": "python \"C:/Users/WesF/Desktop/Dev/Projects/deck-dash/tools/claude_hook.py\"", "timeout": 5 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "python \"C:/Users/WesF/Desktop/Dev/Projects/deck-dash/tools/claude_hook.py\"", "timeout": 5 } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "python \"C:/Users/WesF/Desktop/Dev/Projects/deck-dash/tools/claude_hook.py\"", "timeout": 5 } ] }
    ]
  }
}
```

What each event means to the tile:

| Hook | Session state on the deck |
|---|---|
| `SessionStart` | idle (a dot appears) |
| `UserPromptSubmit` | working (blue, slow pulse) |
| `Notification` | needs you (amber, fast pulse) and a toast; the message is shown in the zoom view |
| `Stop` | done (green) |
| `SessionEnd` | the dot disappears |

Sessions with no event for six hours are dropped. The events file is trimmed to the last 300
lines once it passes 1 MB.

## Trying it without hooks

```
python tools\claude_hook.py --event SessionStart --session demo --cwd C:\Users\WesF\Desktop\Dev\Projects\fpv-sim
python tools\claude_hook.py --event Notification --session demo --message "Permission needed: Bash"
python tools\claude_hook.py --event Stop --session demo
python tools\claude_hook.py --event SessionEnd --session demo
```

# Claude usage tile: statusLine setup

The `usage` tile shows three bars: the context window of the newest session, the 5-hour limit
and the weekly all-models limit. It sits on key 10 and pressing it zooms to a row per meter
with exact token counts and reset countdowns.

The context window comes from the **session transcript** (`~/.claude/projects/*/*.jsonl`), which
every session writes - Desktop included - so that bar needs no setup at all.

The 5-hour and weekly bars are fed by Claude Code's **statusLine** command, not by hooks.

> **The Desktop Code tab does not run the statusLine command.** Measured 2026-09-07: a fresh
> Desktop session fired its hooks into `state/claude-events.jsonl` and never touched
> `state/claude-usage.json`, with the same settings file and the same `python "..."` command
> form. The status line is a terminal element; Desktop renders its own UI and has none to fill.
> So on a Desktop-only machine those two bars stay `-` and the statusLine setup below only helps
> if you also work in a terminal. Claude Code hands that command
a JSON payload on stdin carrying `context_window` and `rate_limits`; `tools/claude_status.py`
caches the numbers in `state/claude-usage.json` (gitignored) and echoes a compact line back, so
the terminal status line reads `ctx 42%  ·  5h 12%  ·  wk 63%` at the same time.

## What to add to `~/.claude/settings.json`

A sibling of `hooks`, not inside it:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python \"C:/Users/WesF/Desktop/Dev/Projects/deck-dash/tools/claude_status.py\"",
    "padding": 0,
    "refreshInterval": 10
  }
}
```

`refreshInterval` re-runs the command every 10 seconds even while Claude is idle, which is what
keeps the deck honest between turns. Without it the numbers only move when the session renders.

## Why there is no weekly Fable bar

Claude Code tracks four rate-limit buckets from its response headers — `five_hour`, `seven_day`,
`seven_day_overage_included` and `overage` — and the statusLine payload narrows that to
`five_hour`, `seven_day` and a gateway-only `spend_limit`. The per-model weekly numbers
(`seven_day_opus`, `seven_day_sonnet`) exist only in the `/api/oauth/usage` API response, which
needs a live OAuth token. deck-dash does not hold one, so the zoom view labels that slot
`Fable: n/a` rather than leaving a silent gap.

## Trying it without a session

```
python tools\claude_status.py --sample tests\fixtures\statusline.json
```

Reads a captured payload instead of stdin and writes `state/claude-usage.json`. If the file is
missing the tile reads `statusline off` rather than showing a false 0%; if the snapshot goes
older than `[claude_usage] stale_minutes` the bars grey out and the key shows its age.


# Plan limits: signing the CLI in

The 5-hour, weekly and per-model windows come from `GET /api/oauth/usage` - the same endpoint
`/usage` reads, and the only source that carries the **per-model** weekly. It needs a signed-in
CLI:

```
claude auth login
```

(`claude auth` on its own only prints help. `claude auth status` shows whether it took.)

`claude setup-token` is **not** enough: those sessions default to `user:inference` scope, and the
endpoint's own schema says `rate_limits` comes back null without `user:profile`.

deck-dash reads `~/.claude/.credentials.json` on each poll and **never writes it**, so a refresh
by the CLI is picked up and nothing here can invalidate your login. When the token has expired the
request is not made at all and the tile says `claude auth login`.

> **The endpoint rate-limits hard.** Two probes earned a 429 with `retry-after: 3264` (~54 min).
> The numbers move slowly - a 5-hour window shifts at most 0.33 % a minute - so `[claude_limits]
> poll_minutes` defaults to 5, and a 429 is honoured to the second rather than retried. Set
> `enabled = false` to switch the poller off entirely.

The access token lasts about eight hours. The CLI refreshes it whenever you use it; if you only
work in the Desktop app, expect to run `claude auth login` again roughly daily, and the limit bars
to dash out until you do.
