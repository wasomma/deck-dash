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

It is fed by Claude Code's **statusLine** command, not by hooks. Claude Code hands that command
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
