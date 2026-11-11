#!/usr/bin/env python3
"""Build docs/demo/index.html: the real dashboard page running on the demo's made-up chats, for the website.

It runs `chatdeck serve` on the demo data, records what the API returns, and writes the dashboard page
with a small fetch shim that answers from that recording, so it works on a static host (GitHub Pages).
Run it again after changing the dashboard:  python3 scripts/site-demo.py
"""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
STATIC = os.path.join(SRC, "chatdeck", "static")
OUT = os.path.join(ROOT, "docs", "demo")

SHIM = r"""<script>
/* Website demo: answers the dashboard's API calls from a recording of `chatdeck demo`. Nothing is sent anywhere. */
(() => {
  const REC = __DATA__;
  const OFF = "This is the demo on the website: it can't open terminals or touch real chats. Install chatdeck to use it for real.";
  const off = Date.now() / 1000 - REC.state.now, day = Math.round(off / 86400);
  const iso = (s) => new Date(Date.parse(s) + off * 1000).toISOString();
  const shiftDay = (d) => { const t = new Date(d + "T12:00:00Z"); t.setUTCDate(t.getUTCDate() + day); return t.toISOString().slice(0, 10); };
  const names = {}, gone = new Set();
  const json = (x) => new Response(JSON.stringify(x), { status: 200, headers: { "Content-Type": "application/json" } });
  const text = (c) => (REC.chats[c.sid]?.items || []).map((i) => i.text || "").join("\n");
  const chats = () => REC.state.chats.filter((c) => !gone.has(c.sid));
  const state = () => {
    const s = JSON.parse(JSON.stringify(REC.state)), t = Date.now() / 1000;
    s.now = t;
    s.chats = s.chats.filter((c) => !gone.has(c.sid)).map((c) => ({ ...c, name: names[c.sid] ?? c.name,
      mtime: c.mtime + off, active: c.active && c.active + off, started: c.started && c.started + off * 1000 }));
    const u = s.usage, L = u.limits;
    u.week = u.week.map(([d, n]) => [shiftDay(d), n]);
    if (L) {
      L.at += off;
      for (const k in L.data || {}) if (L.data[k].resets_at) L.data[k].resets_at += off;
      for (const k in L.forecast || {}) if (L.forecast[k].eta) L.forecast[k].eta += off;
    }
    return s;
  };
  const real = window.fetch.bind(window);
  window.fetch = async (path, opt = {}) => {
    const u = new URL(path, location.href);
    if (!u.pathname.includes("/api/")) return real(path, opt);
    const p = u.pathname.slice(u.pathname.indexOf("/api/")), q = u.searchParams, body = opt.body ? JSON.parse(opt.body) : {};
    await new Promise((r) => setTimeout(r, 60));
    if (p === "/api/state") return json(state());
    if (p === "/api/summary") return json({ running: 0, busy: 0, waiting: [] });
    if (p === "/api/chat") {
      const c = REC.chats[q.get("sid")];
      if (!c || gone.has(q.get("sid"))) return new Response("{}", { status: 404 });
      return json({ ...c, items: c.items.map((i) => (i.ts ? { ...i, ts: iso(i.ts) } : i)) });
    }
    if (p === "/api/search") {
      const ws = (q.get("q") || "").toLowerCase().split(/\s+/).filter(Boolean), hits = {};
      if (ws.length) for (const c of chats()) {
        const t = text(c), lc = t.toLowerCase();
        if (!ws.every((w) => lc.includes(w))) continue;
        const i = lc.indexOf(ws[0]), a = Math.max(0, i - 60), b = Math.min(t.length, i + ws[0].length + 60);
        hits[c.sid] = (a ? "…" : "") + t.slice(a, b).replace(/\n/g, " ") + (b < t.length ? "…" : "");
      }
      return json(hits);
    }
    if (p === "/api/export") {
      const c = REC.state.chats.find((x) => x.sid === q.get("sid"));
      const md = "# " + (names[c.sid] ?? c.name) + "\n\n" + (REC.chats[c.sid]?.items || [])
        .filter((i) => i.k === "user" || i.k === "assistant").map((i) => "**" + (i.k === "user" ? "You" : "Claude") + ":** " + i.text).join("\n\n");
      return new Response(md, { status: 200, headers: { "Content-Type": "text/markdown" } });
    }
    if (p === "/api/rename") {
      if (body.auto) return json({ ok: false, error: OFF });
      const t = String(body.title || "").split(/\s+/).filter(Boolean).join(" ").slice(0, 80);
      if (!t) return json({ ok: false, error: "no such chat" });
      names[body.sid] = t;
      return json({ ok: true, title: t });
    }
    if (p === "/api/delete") {
      const run = new Set(REC.state.chats.filter((c) => c.status).map((c) => c.sid)), done = [], refused = [];
      for (const s of body.sids || []) (run.has(s) ? refused : done).push(s);
      done.forEach((s) => gone.add(s));
      return json({ deleted: done, refused });
    }
    return json({ ok: false, error: OFF });
  };
})();
</script>
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def record():
    sys.path.insert(0, SRC)
    from chatdeck import demo
    root = tempfile.mkdtemp(prefix="chatdeck-site-")
    port = free_port()
    env = dict(os.environ, HOME=root, CHATDECK_DIR=demo.build(root), CHATDECK_DEMO="1", PYTHONPATH=SRC)
    srv = subprocess.Popen([sys.executable, "-m", "chatdeck", "serve", "--port", str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            try: page = urllib.request.urlopen(base + "/").read().decode(); break
            except OSError: time.sleep(0.25)
        else: sys.exit("the demo server didn't start")
        token = re.search(r'const TOKEN="([^"]+)"', page).group(1)
        get = lambda p: json.load(urllib.request.urlopen(urllib.request.Request(base + p, headers={"X-Token": token})))
        state = get("/api/state")
        chats = {c["sid"]: get("/api/chat?sid=" + c["sid"]) for c in state["chats"]}
        return {"state": state, "chats": chats}
    finally:
        srv.terminate(); srv.wait()
        shutil.rmtree(root, ignore_errors=True)


def main():
    rec = record()
    with open(os.path.join(STATIC, "index.html")) as f: page = f.read()
    page = page.replace("__TOKEN__", "demo")
    page = page.replace('<link rel="manifest" href="/manifest.webmanifest">', "")
    page = page.replace('"/logo.svg"', '"logo.svg"').replace('"/logo-192.png"', '"logo-192.png"')
    data = json.dumps(rec, separators=(",", ":")).replace("</", "<\\/")
    page = page.replace("<script>", SHIM.replace("__DATA__", data) + "<script>", 1)
    page = page.replace("<head>", '<head>\n<meta name="robots" content="noindex">', 1)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "index.html"), "w") as f: f.write(page)
    for n in ("logo.svg", "logo-192.png"): shutil.copy(os.path.join(STATIC, n), OUT)
    print(f"wrote {os.path.relpath(OUT, ROOT)}/index.html ({len(rec['state']['chats'])} chats)")


if __name__ == "__main__":
    main()
