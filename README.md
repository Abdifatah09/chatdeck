# Claude Chats dashboard

A small local dashboard for Claude Code: see which chats are running and which need you,
read and search old chats, track usage against your plan limits, and clean up transcripts.
Python standard library only, no dependencies.

## Features

- **Running now** – every live session with its context size and cost. Sessions stuck on a
  permission prompt or a question get an amber **Waiting for you** badge and an optional chime.
- **Saved chats** – read any chat without resuming it, search across all messages,
  rename, export to Markdown, resume in a terminal, delete.
- **Usage** – tokens today and this week, top projects, 24h cost, and your 5-hour and weekly
  limits with a forecast of when you'd hit them at the current pace.
- **Cleanup** – delete empty chats or chats older than 7, 30 or 90 days. Shows disk usage.
  Running chats are never deleted.
- **By project** view: one section per project (git repo, including its worktrees and subfolders)
  with its running and saved chats. Sort by recent, size, cost or messages. Light and dark themes.
  Keyboard shortcuts: `/` search, `j`/`k` move, `Enter` read, `r` resume, `x` select, `Del` delete, `e` rename, `?` help.

## Run

```sh
python3 server.py            # http://127.0.0.1:47123
CLAUDE_DASH_PORT=47124 python3 server.py
```

The server binds to 127.0.0.1 only, and every API call needs a token that changes each run.

## Hooks (optional)

Plan limits come from Claude Code's status line. Have your status-line script save its input:

```sh
input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id",""))')
[ -n "$sid" ] && printf '%s' "$input" > ~/.claude/dash/status/$sid.json
```

To get the "Waiting for you" badge, add this to `~/.claude/settings.json`:

```json
"hooks": {
  "Notification": [{
    "matcher": "permission_prompt|elicitation_dialog",
    "hooks": [{ "type": "command", "command": "python3 /path/to/hooks/waiting.py", "async": true }]
  }]
}
```

## Tests

```sh
python3 -m unittest discover -s tests
```

## Terminals

**Resume**, **Open** and **Agents view** open or find a terminal for you:

| Platform | Opens chats in | Open on a running chat |
|---|---|---|
| WSL | Windows Terminal (new tab) | finds its tab on any virtual desktop and switches to it |
| macOS | Terminal, or iTerm2 if you run the dashboard from it | finds its tab by tty |
| Linux | gnome-terminal, konsole, kitty, alacritty, wezterm, xfce4-terminal, tilix, foot or xterm | finds its window with `wmctrl` or `xdotool` (X11) |

When no terminal can be opened, the dashboard copies the command to paste instead.

Settings (environment variables):

- `CLAUDE_DASH_PORT`: port, default 47123
- `CLAUDE_DASH_TERMINAL`: which terminal to use, e.g. `kitty` or `iterm`
- `CLAUDE_DASH_DIR`: where the dashboard keeps its data, default `~/.claude/dash`
- `CLAUDE_DASH_VDLIB` (WSL): Windows path to [VirtualDesktopLib.cs](https://github.com/MScholtes/VirtualDesktop), which makes
  switching to a chat on another virtual desktop reliable

Finding a running chat works by briefly giving its tab a unique title, so it doesn't work for
tabs whose title is locked (Windows Terminal's `suppressApplicationTitle`).
