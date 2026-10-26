#!/usr/bin/env python3
"""Claude chats dashboard: local-only server for the AOC sidebar.
Lists running sessions (claude agents --json) and saved transcripts under
~/.claude/projects, and can view, search, rename, export and delete saved chats, stop
sessions, and open a chat in Windows Terminal. Binds 127.0.0.1 only; every API call needs
the per-run token embedded in the page (custom header => cross-site pages can't call it)."""
import json, os, glob, secrets, signal, subprocess, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

import transcripts, usage
from transcripts import UUID

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
DATA = os.environ.get("CLAUDE_DASH_DIR") or os.path.join(HOME, ".claude", "dash")
STATUS, WAITING = os.path.join(DATA, "status"), os.path.join(DATA, "waiting")
PORT = int(os.environ.get("CLAUDE_DASH_PORT") or 47123)
TOKEN = secrets.token_hex(16)
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
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

def state():
    act = agents(); chats = INDEX.all(); wait = waiting(act, chats); out = []
    for sid, (p, e) in chats.items():
        a = act.get(sid, {})
        out.append({"sid": sid, "name": a.get("name") or e.title or "(empty)",
                    "cwd": (a.get("cwd") or e.cwd or "").replace(HOME, "~"),
                    "ctx": e.ctx, "msgs": e.msgs, "mtime": e.mtime, "size": e.size, "cost": round(e.cost, 2),
                    "status": a.get("status"), "kind": a.get("kind"), "waiting": wait.get(sid)})
    for sid, a in act.items():  # running but no transcript yet
        if sid not in chats:
            out.append({"sid": sid, "name": a.get("name") or "(new chat)", "cwd": (a.get("cwd") or "").replace(HOME, "~"),
                        "ctx": 0, "msgs": 0, "mtime": time.time(), "size": 0, "cost": 0, "status": a.get("status"),
                        "kind": a.get("kind"), "waiting": wait.get(sid)})
    out.sort(key=lambda x: -x["mtime"])
    return {"chats": out, "now": time.time(), "usage": usage.summary([e for _, e in chats.values()], STATUS, LIMITS)}

VDLIB = os.environ.get("CLAUDE_DASH_VDLIB") or "C:\\Users\\user\\Scripts\\aoc-pin\\VirtualDesktopLib.cs"
FOCUS_PS = r"""
$m = $env:DASH_MARK; $lib = $env:DASH_VDLIB
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class F {
  delegate bool P(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(P cb, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);
  [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr h, int a, out int v, int s);
  public static List<IntPtr> Terminals() { var r = new List<IntPtr>();
    EnumWindows((h, l) => { var c = new StringBuilder(64); GetClassName(h, c, 64);
      if (c.ToString() == "CASCADIA_HOSTING_WINDOW_CLASS") r.Add(h); return true; }, IntPtr.Zero); return r; }
  public static string Title(IntPtr h) { var t = new StringBuilder(512); GetWindowText(h, t, 512); return t.ToString(); }
  public static bool Cloaked(IntPtr h) { int v; DwmGetWindowAttribute(h, 14, out v, 4); return v != 0; }   // on another virtual desktop
  public static bool Raise(IntPtr h) {
    if (IsIconic(h)) ShowWindow(h, 9);
    keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);   // Alt tap lets us take focus
    return SetForegroundWindow(h);
  }
}
'@
$vd = $false
function Show($h) {
  if ([F]::Cloaked($h) -and $lib -and (Test-Path $lib)) {
    if (-not $script:vd) { Add-Type -Path $lib; $script:vd = $true }
    [VirtualDesktop.Desktop]::FromWindow($h).MakeVisible(); Start-Sleep -Milliseconds 250
  }
  [F]::Raise($h) | Out-Null
}
$A = [System.Windows.Automation.AutomationElement]
$tabCond = New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::TabItem)
function Tab($h) {   # the tab carrying the mark, if this window's tabs are readable (not on another desktop)
  foreach ($t in $A::FromHandle($h).FindAll([System.Windows.Automation.TreeScope]::Descendants, $tabCond)) { if ($t.Current.Name -eq $m) { return $t } }
}
for ($i = 0; $i -lt 6; $i++) {   # the terminal takes a moment to pick up the new title
  $wins = [F]::Terminals()
  foreach ($h in $wins) { if ([F]::Title($h) -eq $m) { Show $h; "ok"; exit } }
  foreach ($h in $wins) { if (-not [F]::Cloaked($h)) { $t = Tab $h; if ($t) {
    $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); Show $h; "ok"; exit } } }
  Start-Sleep -Milliseconds 250
}
# Last resort: a background tab in a window on another desktop. Those tabs can only be read once
# the window is on screen, so visit each such window, then go back if the chat isn't there.
$back = [F]::GetForegroundWindow()
foreach ($h in $wins) { if ([F]::Cloaked($h)) { Show $h; Start-Sleep -Milliseconds 300; $t = Tab $h; if ($t) {
  $t.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select(); "ok"; exit } } }
if ($back -ne [IntPtr]::Zero) { Show $back }
"missing"
"""

def focus_terminal(pid, name):
    """Bring the Windows Terminal tab running this Claude process to the front, on whichever
    virtual desktop it is. Window titles alone can't tell chats apart (several can be called
    "raad-iot", and only the active tab's title shows), so the chat's own tab is briefly given a
    unique title through its terminal, found by that, then given its name back."""
    try:
        tty = os.readlink(f"/proc/{pid}/fd/0")
    except OSError:
        return False
    if not tty.startswith("/dev/pts/"): return False
    mark = "claude-dash-" + secrets.token_hex(4)
    def title(t):
        with open(tty, "w") as f: f.write(f"\033]0;{t}\007")
    try:
        title(mark)
        env = dict(os.environ, DASH_MARK=mark, DASH_VDLIB=VDLIB, WSLENV="DASH_MARK:DASH_VDLIB")
        r = subprocess.run(["/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-Command", FOCUS_PS],
                           capture_output=True, text=True, cwd="/mnt/c", env=env, timeout=30)
        return r.stdout.strip().endswith("ok")
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try: title("\u2733 " + name if name else "Claude Code")   # Claude Code's own idle title
        except OSError: pass

def open_running(sid):
    a = agents(fresh=True).get(sid)
    if not a: return {"ok": False, "error": "not running any more"}
    if a.get("kind") == "background":
        cwd = a.get("cwd") if os.path.isdir(a.get("cwd") or "") else HOME
        subprocess.Popen(["/mnt/c/Users/user/AppData/Local/Microsoft/WindowsApps/wt.exe", "-w", "0", "new-tab", "-p", "Ubuntu",
                          "wsl.exe", "-d", "Ubuntu", "--cd", cwd, "--", "bash", "-lic", f"claude attach {a.get('id') or sid}"],
                         cwd="/mnt/c", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "how": "attached in a new terminal tab"}
    if a.get("pid") and focus_terminal(int(a["pid"]), a.get("name")):
        return {"ok": True, "how": "switched to its terminal"}
    return {"ok": False, "error": "couldn't find its tab - it's either outside Windows Terminal or in a tab with a locked title (opened with --suppressApplicationTitle)"}

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
    cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="ignore") if os.path.exists(f"/proc/{pid}") else ""
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
    subprocess.Popen(["/mnt/c/Users/user/AppData/Local/Microsoft/WindowsApps/wt.exe", "-w", "0", "new-tab", "-p", "Ubuntu",
                      "wsl.exe", "-d", "Ubuntu", "--cd", cwd, "--", "bash", "-lic", inner],
                     cwd="/mnt/c", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}

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
        if not self.authed(): return self.send(404, {"error": "not found"})
        if u.path == "/api/state": return self.send(200, state())
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
