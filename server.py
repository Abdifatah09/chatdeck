#!/usr/bin/env python3
"""Claude chats dashboard: local-only server for the AOC sidebar.
Lists running sessions (claude agents --json) and saved transcripts under
~/.claude/projects, and can delete saved chats, stop sessions, and open a chat
in Windows Terminal. Binds 127.0.0.1 only; every API call needs the per-run token
embedded in the page (custom header => cross-site pages can't call it)."""
import json, os, glob, secrets, signal, subprocess, time, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
PORT = 47123
TOKEN = secrets.token_hex(16)
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_cache = {}  # path -> ((mtime, size), info)

import threading
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

def scan(path):
    st = os.stat(path); key = (st.st_mtime, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    title = first = cwd = None; ctx = msgs = 0; days = {}; seen_ids = set()
    with open(path, errors="ignore") as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get("type")
            if t == "summary" and d.get("summary"): title = d["summary"]
            if t == "custom-title" and d.get("customTitle"): title = d["customTitle"]
            if d.get("cwd"): cwd = d["cwd"]
            if t == "user":
                c = d.get("message", {}).get("content")
                if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
                    msgs += 1; first = first or c.strip().replace("\n", " ")[:90]
            elif t == "assistant":
                u = d.get("message", {}).get("usage") or {}
                v = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                if v: ctx = v
                mid = d.get("message", {}).get("id"); ts = d.get("timestamp", "")
                if u and mid and mid not in seen_ids and len(ts) >= 10:
                    seen_ids.add(mid); day = days.setdefault(ts[:10], [0, 0, 0, 0])
                    day[0] += u.get("input_tokens", 0); day[1] += u.get("output_tokens", 0)
                    day[2] += u.get("cache_read_input_tokens", 0); day[3] += u.get("cache_creation_input_tokens", 0)
    info = {"title": title or first, "cwd": cwd, "ctx": ctx, "msgs": msgs, "days": days}
    _cache[path] = (key, info)
    return info

def state():
    act = agents(); out = []
    for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        sid = os.path.basename(p)[:-6]
        if not UUID.match(sid): continue
        i = scan(p); a = act.get(sid, {})
        out.append({"sid": sid, "name": a.get("name") or i["title"] or "(empty)",
                    "cwd": (a.get("cwd") or i["cwd"] or "").replace(HOME, "~"),
                    "ctx": i["ctx"], "msgs": i["msgs"], "mtime": os.path.getmtime(p), "size": os.path.getsize(p),
                    "status": a.get("status"), "kind": a.get("kind")})
    seen = {x["sid"] for x in out}
    for sid, a in act.items():  # running but no transcript yet
        if sid not in seen:
            out.append({"sid": sid, "name": a.get("name") or "(new chat)", "cwd": (a.get("cwd") or "").replace(HOME, "~"),
                        "ctx": 0, "msgs": 0, "mtime": time.time(), "size": 0, "status": a.get("status"), "kind": a.get("kind")})
    out.sort(key=lambda x: -x["mtime"])
    return {"chats": out, "now": time.time(), "usage": usage(glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")))}

def usage(files):
    import datetime
    today = datetime.date.today(); week = {(today - datetime.timedelta(days=i)).isoformat(): 0 for i in range(6, -1, -1)}
    by_proj = {}; tot = {"today": [0, 0, 0, 0], "week": [0, 0, 0, 0]}
    for p in files:
        i = _cache.get(p, (None, {}))[1]; proj = os.path.basename((i.get("cwd") or "").rstrip("/")) or "~"
        for day, v in i.get("days", {}).items():
            if day in week:
                n = v[0] + v[1] + v[3]   # fresh input + output + cache writes (cache reads are cheap re-reads)
                week[day] += n; by_proj[proj] = by_proj.get(proj, 0) + n
                tot["week"] = [a + b for a, b in zip(tot["week"], v)]
                if day == today.isoformat(): tot["today"] = [a + b for a, b in zip(tot["today"], v)]
    limits = None; cost = 0.0
    st = sorted(glob.glob(os.path.join(HOME, ".claude", "dash", "status", "*.json")), key=os.path.getmtime)
    for f in st:
        try:
            d = json.load(open(f))
            if os.path.getmtime(f) > time.time() - 86400: cost += float((d.get("cost") or {}).get("total_cost_usd") or 0)
            if d.get("rate_limits"): limits = {"data": d["rate_limits"], "at": os.path.getmtime(f)}
        except Exception: pass
    return {"week": [[k, v] for k, v in week.items()], "by_project": sorted(by_proj.items(), key=lambda x: -x[1])[:5],
            "today": tot["today"], "week_tot": tot["week"], "limits": limits, "cost_24h": round(cost, 2)}

def focus_window(name):
    """Bring the Windows Terminal window whose title contains this chat name to the front."""
    ps = r"""
$n = $env:CHAT_NAME
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class F {
  delegate bool P(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(P cb, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
  public static bool Focus(string name) {
    IntPtr hit = IntPtr.Zero;
    EnumWindows((h, l) => { var c = new StringBuilder(64); GetClassName(h, c, 64);
      if (c.ToString() != "CASCADIA_HOSTING_WINDOW_CLASS") return true;
      var t = new StringBuilder(256); GetWindowText(h, t, 256);
      if (t.ToString().IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0) { hit = h; return false; } return true; }, IntPtr.Zero);
    if (hit == IntPtr.Zero) return false;
    if (IsIconic(hit)) ShowWindow(hit, 9);
    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);   // Alt tap lets us take focus
    return SetForegroundWindow(hit);
  }
}
'@
if ([F]::Focus($n)) { "ok" } else { "missing" }
"""
    env = dict(os.environ, CHAT_NAME=name, WSLENV="CHAT_NAME")
    r = subprocess.run(["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, cwd="/mnt/c", env=env, timeout=20)
    return "ok" in r.stdout

def open_running(sid):
    a = agents(fresh=True).get(sid)
    if not a: return {"ok": False, "error": "not running any more"}
    if a.get("kind") == "background":
        cwd = a.get("cwd") if os.path.isdir(a.get("cwd") or "") else HOME
        subprocess.Popen(["/mnt/c/Users/user/AppData/Local/Microsoft/WindowsApps/wt.exe", "-w", "0", "new-tab", "-p", "Ubuntu",
                          "wsl.exe", "-d", "Ubuntu", "--cd", cwd, "--", "bash", "-lic", f"claude attach {a.get('id') or sid}"],
                         cwd="/mnt/c", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "how": "attached in a new terminal tab"}
    if a.get("name") and focus_window(a["name"]):
        return {"ok": True, "how": "brought its terminal to the front"}
    return {"ok": False, "error": "it's open in a terminal tab, but that tab isn't the active one in its window - switch to it there"}

def transcript(sid):
    hits = glob.glob(os.path.join(PROJECTS, "*", sid + ".jsonl"))
    return hits[0] if hits else None

def delete(sids):
    act = agents(fresh=True); done, refused = [], []
    for sid in sids:
        if not UUID.match(sid) or sid in act:
            refused.append(sid); continue
        p = transcript(sid)
        if not p or not os.path.realpath(p).startswith(os.path.realpath(PROJECTS) + os.sep):
            refused.append(sid); continue
        os.remove(p); _cache.pop(p, None)
        side = p[:-6]  # sibling folder with this chat's tool output / subagents
        if os.path.isdir(side):
            subprocess.run(["rm", "-rf", "--", side])
        done.append(sid)
    return {"deleted": done, "refused": refused}

def stop(sid):
    a = agents(fresh=True).get(sid)
    if not a or not a.get("pid"): return {"ok": False, "error": "not running"}
    pid = int(a["pid"])
    cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="ignore") if os.path.exists(f"/proc/{pid}") else ""
    if "claude" not in cmd: return {"ok": False, "error": "pid is not a Claude process"}
    os.kill(pid, signal.SIGTERM)
    return {"ok": True}

def open_chat(sid, view=False):
    if view:
        inner = "claude agents"; cwd = HOME
    else:
        if not UUID.match(sid): return {"ok": False}
        p = transcript(sid); cwd = (scan(p)["cwd"] if p else None) or HOME
        inner = f"claude --resume {sid}"
    cwd = cwd if os.path.isdir(cwd) else HOME
    subprocess.Popen(["/mnt/c/Users/user/AppData/Local/Microsoft/WindowsApps/wt.exe", "-w", "0", "new-tab", "-p", "Ubuntu",
                      "wsl.exe", "-d", "Ubuntu", "--cd", cwd, "--", "bash", "-lic", inner],
                     cwd="/mnt/c", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)
    def authed(self):
        return self.headers.get("X-Token") == TOKEN and self.headers.get("Host", "").split(":")[0] in ("localhost", "127.0.0.1")
    def do_GET(self):
        if self.path == "/":
            return self.send(200, open(PAGE).read().replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")
        if self.path == "/api/state" and self.authed():
            return self.send(200, state())
        self.send(404, {"error": "not found"})
    def do_POST(self):
        if not self.authed(): return self.send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/delete": return self.send(200, delete(body.get("sids", [])))
        if self.path == "/api/stop": return self.send(200, stop(body.get("sid", "")))
        if self.path == "/api/open": return self.send(200, open_chat(body.get("sid", ""), body.get("view", False)))
        if self.path == "/api/open-running": return self.send(200, open_running(body.get("sid", "")))
        self.send(404, {"error": "not found"})

if __name__ == "__main__":
    threading.Thread(target=_agents_loop, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
