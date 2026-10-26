"""Where chatdeck keeps its data, and its settings (~/.chatdeck/config.json).
Environment variables (CHATDECK_PORT, CHATDECK_TERMINAL, CHATDECK_VDLIB, CHATDECK_DIR) override the file."""
import json, os

HOME = os.path.expanduser("~")
DATA = os.environ.get("CHATDECK_DIR") or os.path.join(HOME, ".chatdeck")
FILE = os.path.join(DATA, "config.json")
DEFAULT_PORT = 47123
CLAUDE_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
DEMO = bool(os.environ.get("CHATDECK_DEMO"))   # `chatdeck demo`: made-up chats, nothing outside them is touched


def load():
    try:
        with open(FILE) as f: return json.load(f)
    except (OSError, ValueError):
        return {}


def save(cfg):
    os.makedirs(DATA, exist_ok=True)
    tmp = FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(cfg, f, indent=2)
    os.replace(tmp, FILE)


def get(key, default=None):
    env = os.environ.get("CHATDECK_" + key.upper())
    if env: return env
    return load().get(key, default)


def port():
    return int(get("port") or DEFAULT_PORT)
