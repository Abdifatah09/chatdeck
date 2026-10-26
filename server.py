#!/usr/bin/env python3
"""chatdeck: a local dashboard for Claude Code chats.
Lists running sessions (claude agents --json) and saved transcripts under
~/.claude/projects, and can view, search, rename, export and delete saved chats, stop
sessions, and open chats in a terminal (see terminals.py). Binds 127.0.0.1 only; every API call needs
the per-run token embedded in the page (custom header => cross-site pages can't call it)."""
import json, os, glob, secrets, shlex, signal, subprocess, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

import terminals, transcripts, usage
from transcripts import UUID

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
DATA = os.environ.get("CHATDECK_DIR") or os.path.join(HOME, ".chatdeck")
STATUS, WAITING = os.path.join(DATA, "status"), os.path.join(DATA, "waiting")
PORT = int(os.environ.get("CHATDECK_PORT") or 47123)
TOKEN = secrets.token_hex(16)
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
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
        root = cwd.split(os.sep + ".claude" + os.sep + "worktrees" + os.sep)[0]   # Claude's worktrees, even once removed
        try:
            r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                               capture_output=True, text=True, timeout=5)
            common = r.stdout.strip()
            if r.returncode == 0 and os.path.basename(common) == ".git": root = os.path.dirname(common)
        except (OSError, subprocess.TimeoutExpired):
            pass
        _projects[cwd] = root
    return _projects[cwd].replace(HOME, "~", 1)

def state():
    act = agents(); chats = INDEX.all(); wait = waiting(act, chats); out = []
    for sid, (p, e) in chats.items():
        a = act.get(sid, {})
        out.append({"sid": sid, "name": a.get("name") or e.title or "(empty)",
                    "cwd": (a.get("cwd") or e.cwd or "").replace(HOME, "~"), "project": project(a.get("cwd") or e.cwd),
                    "ctx": e.ctx, "msgs": e.msgs, "mtime": e.mtime, "size": e.size, "cost": round(e.cost, 2),
                    "status": a.get("status"), "kind": a.get("kind"), "started": a.get("startedAt"), "waiting": wait.get(sid)})
    for sid, a in act.items():  # running but no transcript yet
        if sid not in chats:
            out.append({"sid": sid, "name": a.get("name") or "(new chat)", "cwd": (a.get("cwd") or "").replace(HOME, "~"),
                        "project": project(a.get("cwd")),
                        "ctx": 0, "msgs": 0, "mtime": time.time(), "size": 0, "cost": 0, "status": a.get("status"),
                        "kind": a.get("kind"), "started": a.get("startedAt"), "waiting": wait.get(sid)})
    out.sort(key=lambda x: -x["mtime"])
    return {"chats": out, "now": time.time(), "usage": usage.summary([e for _, e in chats.values()], STATUS, LIMITS)}

def manual(cwd, command):
    """What to paste into a terminal when no terminal could be opened for the person."""
    return f"cd {shlex.quote(cwd)} && {command}"

def open_running(sid):
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

def rename(sid, title):
    title = " ".join(str(title or "").split())[:80]
    p = transcript(sid)
    if not p or not title: return {"ok": False, "error": "no such chat"}
    if sid in agents(fresh=True):
        return {"ok": False, "error": "it's running - type /rename in the chat instead"}
    transcripts.rename(p, sid, title)
    return {"ok": True, "title": title}

def stop(sid):
    a = agents(fresh=True).get(sid)
    if not a or not a.get("pid"): return {"ok": False, "error": "not running"}
    pid = int(a["pid"])
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    if "claude" not in cmd: return {"ok": False, "error": "pid is not a Claude process"}
    os.kill(pid, signal.SIGTERM)
    return {"ok": True}

def open_chat(sid, view=False):
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
    return dict(transcripts.messages(p), sid=sid, name=a.get("name") or e.title or "(empty)", running=bool(a),
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
        if self.path == "/api/open": return self.send(200, open_chat(body.get("sid", ""), body.get("view", False)))
        if self.path == "/api/open-running": return self.send(200, open_running(body.get("sid", "")))
        self.send(404, {"error": "not found"})

if __name__ == "__main__":
    threading.Thread(target=_agents_loop, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
