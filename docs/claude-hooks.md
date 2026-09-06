# Claude-needs-you tile: hook setup

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
