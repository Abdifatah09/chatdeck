"""chatdeck: a local dashboard for Claude Code chats.
Lists running sessions (claude agents --json) and saved transcripts under
~/.claude/projects, and can view, search, rename, export and delete saved chats, stop
sessions, and open chats in a terminal (see terminals.py). Binds 127.0.0.1 only; every API call needs
the per-run token embedded in the page (custom header => cross-site pages can't call it)."""
import json, os, glob, secrets, shlex, signal, subprocess, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

from . import config, terminals, transcripts, usage
from .transcripts import UUID

HOME = config.HOME
PROJECTS = os.path.join(HOME, ".claude", "projects")
DATA = config.DATA
STATUS, WAITING = os.path.join(DATA, "status"), os.path.join(DATA, "waiting")
TOKEN = secrets.token_hex(16)
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAGE = os.path.join(ASSETS, "index.html")
STATIC = {"/logo.svg": ("logo.svg", "image/svg+xml"), "/logo-192.png": ("logo-192.png", "image/png"),
          "/logo-512.png": ("logo-512.png", "image/png")}
MANIFEST = {"name": "chatdeck", "short_name": "chatdeck", "display": "standalone", "background_color": "#0f1115",
            "theme_color": "#0f1115", "start_url": "/",
            "icons": [{"src": "/logo-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/logo-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "/logo.svg", "sizes": "any", "type": "image/svg+xml"}]}
INDEX = transcripts.Index(PROJECTS)
LIMITS = usage.LimitHistory(os.path.join(DATA, "limits-history.jsonl"))

_agents = (0.0, {})
_agents_lock = threading.Lock()
def _fetch_agents():
    global _agents
    try:
        if config.DEMO:
            with open(os.path.join(DATA, "demo-agents.json")) as f: out = f.read()
        else:
            out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=30).stdout
        res = {s["sessionId"]: s for s in json.loads(out or "[]") if s.get("sessionId")}
        with _agents_lock: _agents = (time.time(), res)
    except Exception:
        pass
    return _agents[1]

def _agents_loop():
    while True:
        _fetch_agents(); time.sleep(4)

def agents(fresh=False):
    """Running sessions. Normal reads use the list a background thread keeps fresh, so pages never wait;
    destructive actions (delete/stop) ask for a fresh copy first."""
    return _fetch_agents() if fresh else _agents[1]

def waiting(act, chats):
    """{sid: message} for running chats stuck on a permission prompt or question. A mark from the
    waiting hook counts until the transcript is written to after it (the prompt was answered)."""
    out = {}
    for f in glob.glob(os.path.join(WAITING, "*.json")):
        sid = os.path.basename(f)[:-5]
        try:
            if sid not in act:
                if os.path.getmtime(f) < time.time() - 3600: os.remove(f)
                continue
            with open(f) as fh: w = json.load(fh)
            hit = chats.get(sid)
            if not hit or hit[1].mtime <= w["at"] + 1.5:
                out[sid] = w.get("message") or "Waiting for you"
        except (OSError, ValueError, KeyError):
            pass
    return out

_projects = {}
def project(cwd):
    """The project a folder belongs to: the main checkout of its git repo (so subfolders and
    worktrees count as the repo itself), or the folder when it isn't in a repo."""
    if not cwd: return ""
    if cwd not in _projects:
        # Claude's worktrees, even once removed; resolved like git resolves paths (macOS: /var is /private/var)
        root = os.path.realpath(cwd.split(os.sep + ".claude" + os.sep + "worktrees" + os.sep)[0])
        try:
            r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                               capture_output=True, text=True, timeout=5)
            common = r.stdout.strip()
            if r.returncode == 0 and os.path.basename(common) == ".git": root = os.path.dirname(common)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _projects[cwd] = root
    return _projects[cwd].replace(HOME, "~", 1)

# Names given to chats while they were running. Claude Code keeps a running chat's name in memory
# and rewrites it, so the new name is shown from here and written into the chat once it ends.
PENDING = os.path.join(DATA, "pending-names.json")
_pending_lock = threading.Lock()

def pending_names():
    try:
        with open(PENDING) as f: return json.load(f)
    except (OSError, ValueError):
        return {}

def _save_pending(names):
    os.makedirs(DATA, exist_ok=True)
    with open(PENDING + ".tmp", "w") as f: json.dump(names, f)
    os.replace(PENDING + ".tmp", PENDING)

def apply_pending(act, paths):
    """Write pending names into chats that have stopped ({sid: transcript path}); returns {sid: name} still pending.
    A name is dropped instead if the chat was renamed in Claude Code meanwhile (/rename there wins)."""
    with _pending_lock:
        names = pending_names(); changed = False
        for sid, p in list(names.items()):
            renamed_there = sid in act and act[sid].get("name") not in (p["was"], p["title"])
            if sid in act and not renamed_there: continue
            if not renamed_there and sid in paths: transcripts.rename(paths[sid], sid, p["title"])
            del names[sid]; changed = True
        if changed: _save_pending(names)
        return {sid: p["title"] for sid, p in names.items()}

def state():
    act = agents(); pend = apply_pending(act, INDEX.files())   # before reading chats, so a name just written shows
    chats = INDEX.all(); wait = waiting(act, chats); out = []
    for sid, (p, e) in chats.items():
        a = act.get(sid, {})
        out.append({"sid": sid, "name": pend.get(sid) or a.get("name") or e.title or "(empty)",
                    "cwd": (a.get("cwd") or e.cwd or "").replace(HOME, "~"), "project": project(a.get("cwd") or e.cwd),
                    "ctx": e.ctx, "msgs": e.msgs, "mtime": e.mtime, "size": e.size, "cost": round(e.cost, 2),
                    "status": a.get("status"), "kind": a.get("kind"), "started": a.get("startedAt"), "waiting": wait.get(sid)})
    for sid, a in act.items():  # running but no transcript yet
        if sid not in chats:
            out.append({"sid": sid, "name": pend.get(sid) or a.get("name") or "(new chat)", "cwd": (a.get("cwd") or "").replace(HOME, "~"),
                        "project": project(a.get("cwd")),
                        "ctx": 0, "msgs": 0, "mtime": time.time(), "size": 0, "cost": 0, "status": a.get("status"),
                        "kind": a.get("kind"), "started": a.get("startedAt"), "waiting": wait.get(sid)})
    out.sort(key=lambda x: -x["mtime"])
    return {"chats": out, "now": time.time(), "usage": usage.summary([e for _, e in chats.values()], STATUS, LIMITS, project=project)}

def manual(cwd, command):
    """What to paste into a terminal when no terminal could be opened for the person."""
    return f"cd {shlex.quote(cwd)} && {command}"

DEMO_OFF = {"ok": False, "error": "this is the demo - it doesn't open terminals or touch real chats"}

def open_running(sid):
    if config.DEMO: return DEMO_OFF
    a = agents(fresh=True).get(sid)
    if not a: return {"ok": False, "error": "not running any more"}
    cwd = a.get("cwd") if os.path.isdir(a.get("cwd") or "") else HOME
    if a.get("kind") == "background":
        cmd = f"claude attach {a.get('id') or sid}"
        if terminals.launch(cwd, cmd): return {"ok": True, "how": "attached in a new terminal tab"}
        return {"ok": False, "copy": manual(cwd, cmd)}
    if a.get("pid") and terminals.focus(int(a["pid"]), a.get("name")):
        return {"ok": True, "how": "switched to its terminal"}
    return {"ok": False, "error": "couldn't find its terminal tab - switch to it yourself (tabs with a locked title can't be found)"}

def transcript(sid):
    if not UUID.match(sid or ""): return None
    hits = glob.glob(os.path.join(PROJECTS, "*", sid + ".jsonl"))
    if not hits or not os.path.realpath(hits[0]).startswith(os.path.realpath(PROJECTS) + os.sep): return None
    return hits[0]

def delete(sids):
    act = agents(fresh=True); done, refused = [], []
    for sid in sids:
        p = transcript(sid)
        if not p or sid in act:
            refused.append(sid); continue
        os.remove(p); INDEX.forget(p)
        side = p[:-6]  # sibling folder with this chat's tool output / subagents
        if os.path.isdir(side):
            subprocess.run(["rm", "-rf", "--", side])
        done.append(sid)
    return {"deleted": done, "refused": refused}

def _demo_rename(sid, title):
    path = os.path.join(DATA, "demo-agents.json")
    with open(path) as f: running = json.load(f)
    for x in running:
        if x["sessionId"] == sid: x["name"] = title
    with open(path, "w") as f: json.dump(running, f)
    _fetch_agents()
    return {"ok": True, "title": title, "typed": True}

def rename(sid, title):
    title = " ".join(str(title or "").split())[:80]
    p = transcript(sid)
    if not p or not title: return {"ok": False, "error": "no such chat"}
    act = agents(fresh=True)
    if sid in act:
        a = act[sid]
        # Idle at its prompt in a terminal: run Claude Code's own /rename in it, so the name changes everywhere.
        idle = a.get("status") == "idle" and a.get("kind") == "interactive" and sid not in waiting(act, INDEX.all())
        if config.DEMO: return _demo_rename(sid, title)
        if idle and a.get("pid") and terminals.type_into(int(a["pid"]), a.get("name"), f"/rename {title}"):
            return {"ok": True, "title": title, "typed": True}
        with _pending_lock:   # busy, waiting on you, or not in a terminal we can reach: see PENDING
            names = pending_names(); names[sid] = {"title": title, "was": a.get("name")}; _save_pending(names)
        return {"ok": True, "title": title, "pending": True, "busy": not idle}
    transcripts.rename(p, sid, title)
    return {"ok": True, "title": title}

def compact(sid):
    """Run Claude Code's /compact in a running chat, the same way rename types /rename."""
    act = agents(fresh=True); a = act.get(sid)
    if not a: return {"ok": False, "error": "it isn't running"}
    if a.get("status") != "idle" or sid in waiting(act, INDEX.all()):
        return {"ok": False, "error": "it's busy - try again when it's waiting for your next message"}
    if config.DEMO: return DEMO_OFF
    if a.get("kind") != "interactive" or not a.get("pid") or not terminals.type_into(int(a["pid"]), a.get("name"), "/compact"):
        return {"ok": False, "error": "couldn't reach its terminal - type /compact in it"}
    return {"ok": True}

def stop(sid):
    if config.DEMO: return DEMO_OFF
    a = agents(fresh=True).get(sid)
    if not a or not a.get("pid"): return {"ok": False, "error": "not running"}
    pid = int(a["pid"])
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    if "claude" not in cmd: return {"ok": False, "error": "pid is not a Claude process"}
    os.kill(pid, signal.SIGTERM)
    return {"ok": True}

def open_chat(sid, view=False):
    if config.DEMO: return DEMO_OFF
    if view:
        inner = "claude agents"; cwd = HOME
    else:
        p = transcript(sid)
        if not p: return {"ok": False}
        cwd = INDEX.get(p).cwd or HOME
        inner = f"claude --resume {sid}"
    cwd = cwd if os.path.isdir(cwd) else HOME
    if terminals.launch(cwd, inner): return {"ok": True}
    return {"ok": False, "copy": manual(cwd, inner)}

def chat(sid):
    p = transcript(sid)
    if not p: return None
    e = INDEX.get(p); a = agents().get(sid, {})
    return dict(transcripts.messages(p), sid=sid, name=(pending_names().get(sid) or {}).get("title") or a.get("name") or e.title or "(empty)", running=bool(a),
                cwd=(e.cwd or "").replace(HOME, "~"), cost=round(e.cost, 2), size=e.size, mtime=e.mtime)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json", extra=()):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def authed(self):
        return self.headers.get("X-Token") == TOKEN and self.headers.get("Host", "").split(":")[0] in ("localhost", "127.0.0.1")
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            with open(PAGE) as f:
                return self.send(200, f.read().replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
        if u.path in STATIC:   # the logo and app manifest are public; they give nothing away
            name, ctype = STATIC[u.path]
            with open(os.path.join(ASSETS, name), "rb") as f:
                return self.send(200, f.read(), ctype)
        if u.path == "/manifest.webmanifest":
            return self.send(200, json.dumps(MANIFEST).encode(), "application/manifest+json")
        if not self.authed(): return self.send(404, {"error": "not found"})
        if u.path == "/api/state": return self.send(200, state())
        if u.path == "/api/summary":   # small payload for the desktop bubble's badge
            act = agents(); w = waiting(act, INDEX.all())
            return self.send(200, {"running": len(act), "busy": sum(a.get("status") == "busy" for a in act.values()),
                                   "waiting": [act[sid].get("name") or "a chat" for sid in w if sid in act]})
        if u.path == "/api/search": return self.send(200, INDEX.search(q.get("q", "")[:200]))
        if u.path == "/api/chat":
            c = chat(q.get("sid", ""))
            return self.send(200, c) if c else self.send(404, {"error": "no such chat"})
        if u.path == "/api/export":
            c = transcript(q.get("sid", ""))
            if not c: return self.send(404, {"error": "no such chat"})
            title = INDEX.get(c).title or "chat"
            name = "".join(ch if ch.isalnum() or ch in "-_ " else "-" for ch in title).strip()[:60] or "chat"
            return self.send(200, transcripts.markdown(c, title).encode(), "text/markdown; charset=utf-8",
                             [("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}.md")])
        self.send(404, {"error": "not found"})
    def do_POST(self):
        if not self.authed(): return self.send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/delete": return self.send(200, delete(body.get("sids", [])))
        if self.path == "/api/rename": return self.send(200, rename(body.get("sid", ""), body.get("title", "")))
        if self.path == "/api/stop": return self.send(200, stop(body.get("sid", "")))
        if self.path == "/api/compact": return self.send(200, compact(body.get("sid", "")))
        if self.path == "/api/open": return self.send(200, open_chat(body.get("sid", ""), body.get("view", False)))
        if self.path == "/api/open-running": return self.send(200, open_running(body.get("sid", "")))
        self.send(404, {"error": "not found"})

def serve(port):
    """Run the dashboard until stopped. Raises OSError if the port is taken."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    os.makedirs(DATA, exist_ok=True)
    pidfile = os.path.join(DATA, "server.pid")
    with open(pidfile, "w") as f: f.write(str(os.getpid()))
    threading.Thread(target=_agents_loop, daemon=True).start()
    try:
        httpd.serve_forever()
    finally:
        try: os.remove(pidfile)
        except OSError: pass
