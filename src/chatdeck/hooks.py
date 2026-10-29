"""Claude Code hooks chatdeck relies on, and installing them into ~/.claude/settings.json.

- Status line (`chatdeck hook statusline`): saves each session's status payload (plan limits,
  cost) for the usage panel, then runs the status line the person already had, if any, so
  their status line looks the same as before.
- Notification (`chatdeck hook waiting`): marks a chat as waiting on a permission prompt or a
  question. The dashboard treats the mark as cleared once the transcript moves on."""
import json, os, re, shlex, shutil, subprocess, sys, time

from . import config

WAITING_MATCHER = "permission_prompt|elicitation_dialog"
LEGACY = ("hooks/waiting.py",)   # hook commands from before chatdeck was a package


def _write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f: f.write(data)
    os.replace(path + ".tmp", path)


# --- running the hooks ----------------------------------------------------------------------

def statusline(stdin=sys.stdin, stdout=sys.stdout):
    raw = stdin.read()
    try:
        sid = json.loads(raw).get("session_id", "")
        if re.fullmatch(r"[0-9a-f-]{36}", sid):
            _write_atomic(os.path.join(config.DATA, "status", sid + ".json"), raw)
    except (ValueError, AttributeError, OSError):
        pass
    wrapped = config.load().get("statusline_command")
    if wrapped:
        try:
            r = subprocess.run(wrapped, shell=True, input=raw, capture_output=True, text=True, timeout=10)
            stdout.write(r.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass


def waiting(stdin=sys.stdin):
    try:
        d = json.load(stdin)
        sid = d.get("session_id", "")
        if re.fullmatch(r"[0-9a-f-]{36}", sid):
            _write_atomic(os.path.join(config.DATA, "waiting", sid + ".json"),
                          json.dumps({"at": time.time(), "message": d.get("message", ""), "type": d.get("notification_type", "")}))
    except (ValueError, AttributeError, OSError):
        pass


# --- installing them ------------------------------------------------------------------------

def command():
    """How settings.json should call chatdeck: the absolute path of the installed command,
    or this Python running the package from wherever it lives."""
    exe = shutil.which("chatdeck")
    if exe: return shlex.quote(exe)
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"PYTHONPATH={shlex.quote(src)} {shlex.quote(sys.executable)} -m chatdeck"


def _ours(cmd):
    return " hook statusline" in cmd or " hook waiting" in cmd or any(l in cmd for l in LEGACY)


def _load(path):
    try:
        with open(path) as f: return json.load(f)
    except FileNotFoundError:
        return {}


def _save(path, settings):
    if os.path.exists(path):
        backups = os.path.join(config.DATA, "backups"); os.makedirs(backups, exist_ok=True)
        shutil.copy2(path, os.path.join(backups, "settings.json." + time.strftime("%Y%m%d-%H%M%S")))
    _write_atomic(path, json.dumps(settings, indent=2) + "\n")


def _drop_ours(settings):
    """Remove chatdeck's Notification hooks (any version), leaving everything else as it was."""
    groups = (settings.get("hooks") or {}).get("Notification") or []
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if not _ours(h.get("command", ""))]
    groups[:] = [g for g in groups if g.get("hooks")]
    if "hooks" in settings and not groups:
        settings["hooks"].pop("Notification", None)
        if not settings["hooks"]: settings.pop("hooks")


def install(path=config.CLAUDE_SETTINGS, cmd=None):
    """Add chatdeck's hooks. Safe to run again. Returns a list of what changed."""
    cmd = cmd or command(); settings = _load(path); cfg = config.load(); done = []
    ours = f"{cmd} hook statusline"
    sl = settings.get("statusLine") or {}
    current = sl.get("command", "")
    if current != ours:
        if current and not _ours(current):
            cfg["statusline_command"] = current
            done.append(f"status line: kept yours ({current}) and saves usage before running it")
        else:
            done.append("status line: saves usage for the dashboard")
        settings["statusLine"] = {**sl, "type": "command", "command": ours}
    _drop_ours(settings)
    groups = settings.setdefault("hooks", {}).setdefault("Notification", [])
    hook = {"type": "command", "command": f"{cmd} hook waiting", "async": True}
    group = next((g for g in groups if g.get("matcher") == WAITING_MATCHER), None)
    if group: group.setdefault("hooks", []).append(hook)
    else: groups.append({"matcher": WAITING_MATCHER, "hooks": [hook]})
    done.append("notification hook: flags chats waiting for you")
    _save(path, settings); config.save(cfg)
    return done


def uninstall(path=config.CLAUDE_SETTINGS):
    """Take chatdeck's hooks out again and put back the status line that was there before."""
    settings = _load(path); cfg = config.load(); done = []
    sl = settings.get("statusLine") or {}
    if _ours(sl.get("command", "")):
        prev = cfg.pop("statusline_command", None)
        if prev:
            settings["statusLine"] = {**sl, "command": prev}; done.append(f"status line: back to {prev}")
        else:
            settings.pop("statusLine"); done.append("status line: removed")
    before = json.dumps(settings.get("hooks"))
    _drop_ours(settings)
    if json.dumps(settings.get("hooks")) != before: done.append("notification hook: removed")
    if done:
        _save(path, settings); config.save(cfg)
    return done
