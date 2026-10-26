// Records chatdeck's screenshots, GIFs and videos from `chatdeck demo`, so they can be redone
// whenever the UI changes:
//
//   cd scripts/promo && npm install && npm run record            # everything
//   npm run record -- shots                                       # just screenshots
//   npm run record -- tour search                                 # just these clips
//
// Writes README screenshots to docs/ and the rest (GIFs, MP4s, framed shots, social card) to docs/media/.
// Needs Playwright's headless Chromium: `npx playwright-core install chromium-headless-shell`.
import { spawn, execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import ffmpeg from "ffmpeg-static";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
const DOCS = path.join(ROOT, "docs"), MEDIA = path.join(DOCS, "media");
const PORT = 47291;
const URL_ = `http://127.0.0.1:${PORT}/`;
const STAGE = { width: 1600, height: 900 }, STAGE_SCALE = 1.5;   // the video frame: 2400x1350, encoded at 1920x1080
const APP = { width: 1240, height: 700 };                         // the dashboard's window inside it
const GIF_WIDTH = 960;
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---- the demo server -------------------------------------------------------------------------------

async function startDemo() {
  const srv = spawn("python3", ["-m", "chatdeck", "demo", "--no-open", "--port", String(PORT)], {
    env: { ...process.env, PYTHONPATH: path.join(ROOT, "src") }, stdio: ["ignore", "ignore", "inherit"], detached: true });
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(URL_)).ok) return srv; } catch {}
    await sleep(250);
  }
  await stopDemo(srv);
  throw new Error(`chatdeck demo didn't come up on port ${PORT}`);
}

// Like Ctrl+C in a terminal: the whole process group, so the server under `chatdeck demo` stops too and
// the demo deletes its temp folder.
const stopDemo = srv => new Promise(r => { srv.once("exit", r); process.kill(-srv.pid, "SIGINT"); });

// ---- the page: fonts ------------------------------------------------------------------------

// Headless Chromium on Linux falls back to DejaVu for "Segoe UI"; use the real one when Windows fonts
// are reachable (WSL), so the shots look like the app does on the desktop.
const WINFONTS = "/mnt/c/Windows/Fonts";
const FONTS = [["segoeui.ttf", 400, "normal"], ["seguisb.ttf", 600, "normal"], ["segoeuib.ttf", 700, "normal"],
               ["segoeuii.ttf", 400, "italic"]].filter(([f]) => fs.existsSync(path.join(WINFONTS, f)));
const MONO = ["consola.ttf", "consolab.ttf"].filter(f => fs.existsSync(path.join(WINFONTS, f)));
const FONT_CSS = FONTS.map(([f, w, s]) => `@font-face{font-family:"Segoe UI";src:url(/__font/${f});font-weight:${w};font-style:${s}}`)
  .concat(MONO.map((f, i) => `@font-face{font-family:Consolas;src:url(/__font/${f});font-weight:${i ? 700 : 400}}`)).join("");

// The same fonts inline, for pages made with setContent (no origin for /__font/ to resolve against).
const FONT_CSS_INLINE = FONT_CSS.replace(/url\(\/__font\/([^)]+)\)/g,
  (_, f) => `url(data:font/ttf;base64,${fs.readFileSync(path.join(WINFONTS, f)).toString("base64")})`);

const FONT_SCRIPT = `addEventListener("DOMContentLoaded", () => {
  const st = document.createElement("style"); st.textContent = ${JSON.stringify(FONT_CSS)}; document.head.append(st) })`;

async function newPage(browser, { width, height, scale = 2, theme = "dark" }) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: scale, colorScheme: theme });
  await ctx.route("**/__font/*", r => r.fulfill({ path: path.join(WINFONTS, path.basename(new URL(r.request().url()).pathname)) }));
  await ctx.addInitScript(FONT_SCRIPT);
  const page = await ctx.newPage();
  await page.goto(URL_);
  await page.waitForSelector("#list .card");
  await page.evaluate(() => document.fonts.ready);
  await sleep(400);
  return page;
}

// ---- the stage: the app in a window on a backdrop, with captions, title cards and a camera -----------
//
// The videos are recorded from a "stage" page that shows the dashboard in an iframe (same origin, so it
// works as usual). Everything around it - the cursor, captions, zooming in - is drawn on the stage.
//
// They're rendered frame by frame on a virtual clock rather than screen-recorded: each frame advances the
// clock by 1/30 s, sets every animation to where it should be at that moment, and is captured. Capturing
// is slower than real time, so a live recording drops frames exactly when the camera moves; this doesn't.
// Scenes wait with hold(ms), which renders that much video, never with sleep().

const FPS = 30, FRAME = 1000 / FPS;
const CAPTION_SPACE = 120;   // stage px at the bottom kept for the caption when framing a zoom

const LOGO = "data:image/svg+xml;base64," + fs.readFileSync(path.join(ROOT, "src/chatdeck/static/logo.svg")).toString("base64");
const INSTALL = "pipx install chatdeck";

const STAGE_HTML = `<!doctype html><html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box}html,body{margin:0;height:100%;overflow:hidden}
  body{font-family:"Segoe UI",system-ui,sans-serif;color:#e6e8ee;
    background:radial-gradient(1400px 800px at 85% -10%,#2b4a86 0%,transparent 60%),radial-gradient(1000px 700px at -10% 110%,#1f5a46 0%,transparent 55%),#0b0d12}
  #cam{position:absolute;inset:0;transform-origin:0 0}
  .win{position:absolute;left:${(STAGE.width - APP.width) / 2}px;top:44px;border-radius:12px;overflow:hidden;background:#0f1115;
    box-shadow:0 30px 90px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.09)}
  .bar{height:34px;background:#1a1e26;display:flex;align-items:center;gap:8px;padding:0 14px;position:relative}
  .bar i{width:12px;height:12px;border-radius:50%}
  .bar span{position:absolute;left:0;right:0;text-align:center;font-size:13px;color:#8b92a1;pointer-events:none}
  iframe{display:block;border:0;width:${APP.width}px;height:${APP.height}px}

  #cap{position:absolute;left:50%;bottom:30px;opacity:0;pointer-events:none;
    background:rgba(13,15,20,.88);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.12);border-radius:14px;
    padding:14px 26px 15px;box-shadow:0 16px 50px rgba(0,0,0,.5);text-align:center;white-space:nowrap}
  #cap .k{font-size:12.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#7fb0ff;margin-bottom:3px}
  #cap .t{font-size:25px;font-weight:600;line-height:1.3}
  #cap .t em{font-style:normal;color:#f5b942}
  kbd{display:inline-block;min-width:30px;padding:1px 8px;margin:0 2px;border-radius:7px;font:600 19px Consolas,ui-monospace,monospace;
    background:#222733;border:1px solid #3a4150;border-bottom-width:3px;color:#e6e8ee;vertical-align:2px}
  kbd.hit{background:#4a7fd4;border-color:#7fb0ff;transform:translateY(2px)}

  #card{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;
    background:inherit;opacity:0;pointer-events:none;z-index:5}
  #card .logo{display:flex;align-items:center;gap:20px;font-size:78px;font-weight:700;letter-spacing:-1px}
  #card .logo img{width:84px;height:84px}
  #card .line{font-size:38px;font-weight:600}
  #card .sub{font-size:24px;color:#a9b0be}
  #card .cmd{font:24px Consolas,monospace;background:#171a21;border:1px solid #2c3140;border-radius:10px;padding:14px 22px}
  #card .cmd b{color:#8b92a1;font-weight:400}

  #cur{position:absolute;left:0;top:0;width:30px;height:30px;z-index:9;pointer-events:none;opacity:0;filter:drop-shadow(0 2px 3px rgba(0,0,0,.5))}
  .ring{position:absolute;width:46px;height:46px;margin:-23px 0 0 -23px;border-radius:50%;border:3px solid #7fb0ff;z-index:8;pointer-events:none}
</style></head><body>
<div id="cam"><div class="win"><div class="bar"><i style="background:#f06a6a"></i><i style="background:#f5b942"></i><i style="background:#3ecf8e"></i>
  <span>chatdeck</span></div><iframe src="/"></iframe></div></div>
<div id="cap"><div class="k"></div><div class="t"></div></div>
<div id="card"></div>
<div id="cur"><svg viewBox="0 0 22 22" width="30" height="30"><path d="M3 2l14 9.5-6.2 1.1 3.6 7.1-2.6 1.3-3.6-7.2L3 18z" fill="#fff" stroke="#111" stroke-width="1.3" stroke-linejoin="round"/></svg></div>
<script>
  // Every animation is a value tweened from -> to over [t0, t0 + d] of the virtual clock; render(T) draws time T.
  const $ = id => document.getElementById(id), lerp = (a, b, t) => a + (b - a) * t;
  const inOut = t => t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2, out = t => 1 - (1 - t) ** 3;
  const prog = (a, T) => a.d ? Math.min(1, Math.max(0, (T - a.t0) / a.d)) : 1;
  const A = { cam: { from: { x: 0, y: 0, z: 1 }, to: { x: 0, y: 0, z: 1 }, t0: 0, d: 0 },
              cap: { from: 0, to: 0, t0: 0, d: 0 }, card: { from: 0, to: 0, t0: 0, d: 0 }, cur: { from: 0, to: 0, t0: 0, d: 0 } };
  const rings = [], hits = [];
  window.now = 0;
  window.camAt = T => { const c = A.cam, k = inOut(prog(c, T));
    return { x: lerp(c.from.x, c.to.x, k), y: lerp(c.from.y, c.to.y, k), z: lerp(c.from.z, c.to.z, k) } };
  const val = (a, T) => lerp(a.from, a.to, out(prog(a, T)));
  window.tween = (name, to, d) => { A[name] = { from: name === "cam" ? camAt(now) : val(A[name], now), to, t0: now, d } };
  window.camTarget = () => A.cam.to;
  window.cursorAt = (x, y) => { $("cur").style.transform = \`translate(\${x - 4}px,\${y - 3}px)\` };
  window.ring = (x, y) => { const r = document.createElement("div"); r.className = "ring"; r.style.left = x + "px"; r.style.top = y + "px";
    document.body.append(r); rings.push({ el: r, t0: now }) };
  window.hitKey = label => { const el = [...document.querySelectorAll("#cap kbd")].find(e => e.textContent === label); if (el) hits.push({ el, t0: now }) };
  window.setCaption = (k, t) => { $("cap").querySelector(".k").textContent = k; $("cap").querySelector(".t").innerHTML = t };
  window.setCard = h => { $("card").innerHTML = h };

  window.render = T => {
    now = T;
    const c = camAt(T); $("cam").style.transform = \`translate(\${c.x}px,\${c.y}px) scale(\${c.z})\`;
    const o = val(A.cap, T); $("cap").style.opacity = o; $("cap").style.transform = \`translate(-50%,\${(1 - o) * 22}px)\`;
    const co = val(A.card, T); $("card").style.opacity = co;
    [...$("card").children].forEach((el, i) => {   // the card's lines rise in one after another
      const p = A.card.to ? out(Math.min(1, Math.max(0, (T - A.card.t0 - 250 * i) / 700))) : 1;
      el.style.opacity = p; el.style.transform = \`translateY(\${(1 - p) * 16}px)\` });
    $("cur").style.opacity = val(A.cur, T);
    for (const r of [...rings]) { const p = (T - r.t0) / 500;
      if (p >= 1) { r.el.remove(); rings.splice(rings.indexOf(r), 1) }
      else { r.el.style.opacity = 1 - p; r.el.style.transform = \`scale(\${lerp(.3, 1.25, out(p))})\` } }
    for (const h of [...hits]) { const on = T - h.t0 < 220; h.el.classList.toggle("hit", on); if (!on) hits.splice(hits.indexOf(h), 1) }
  };
</script></body></html>`;

async function newStage(browser) {
  const ctx = await browser.newContext({ viewport: STAGE, deviceScaleFactor: STAGE_SCALE, colorScheme: "dark" });
  await ctx.route("**/__font/*", r => r.fulfill({ path: path.join(WINFONTS, path.basename(new URL(r.request().url()).pathname)) }));
  await ctx.route(URL_ + "__stage", r => r.fulfill({ contentType: "text/html", body: STAGE_HTML }));
  await ctx.addInitScript(FONT_SCRIPT);
  const page = await ctx.newPage();
  await page.goto(URL_ + "__stage");
  const app = await (await page.waitForSelector("iframe")).contentFrame();
  await app.waitForSelector("#list .card");
  await Promise.all([page, app].map(f => f.evaluate(() => document.fonts.ready)));
  await sleep(400);
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Animation.enable");
  return { page, app, cdp, T: 0, n: 0, dir: null, started: 0, capOn: false,
           mouse: { x: STAGE.width * .62, y: STAGE.height * .6 } };
}

const ease = t => t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
const loc = (s, target) => typeof target === "string" ? s.app.locator(target).first() : target;
const tween = (s, name, to, d) => s.page.evaluate(([n, to, d]) => tween(n, to, d), [name, to, d]);

// One frame of video: move the clock on, draw that moment, capture it.
async function frame(s) {
  s.T += FRAME;
  await s.page.evaluate(T => render(T), s.T);
  const { data } = await s.cdp.send("Page.captureScreenshot", { format: "jpeg", quality: 92 });
  fs.writeFileSync(path.join(s.dir, `${String(s.n++).padStart(5, "0")}.jpg`), Buffer.from(data, "base64"));
  // The app's own CSS animations (the pulsing dots) run on the real clock; slow them to match the video's.
  if (s.n % 30 === 0) {
    const real = (Date.now() - s.started) / s.n;
    await s.cdp.send("Animation.setPlaybackRate", { playbackRate: Math.min(1, FRAME / real) });
  }
}
async function hold(s, ms) { for (let i = Math.round(ms / FRAME); i > 0; i--) await frame(s); }

// Where an element of the app is on the stage (at the current camera). Playwright's boundingBox() leaves
// out the camera's scale for elements inside the iframe, so map it ourselves.
async function box(s, l) {
  const r = await l.evaluate(e => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height } });
  const c = await s.page.evaluate(() => { const f = document.querySelector("iframe"); return { ...camAt(now), fx: f.offsetLeft + f.parentElement.offsetLeft, fy: f.offsetTop + f.parentElement.offsetTop } });
  return { x: c.x + c.z * (c.fx + r.x), y: c.y + c.z * (c.fy + r.y), width: r.width * c.z, height: r.height * c.z };
}

// Is the element inside the app's scrolled view (not necessarily inside the camera's frame)?
const inApp = l => l.evaluate(e => { const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && r.height > 0 });

// Scroll the app so the element sits about a quarter of the way down, frame by frame.
async function reveal(s, l, ms = 700) {
  const [sel, by] = await l.evaluate(e => {
    const sc = e.closest("#vBody") || document.querySelector("main"), r = e.getBoundingClientRect(), v = sc.getBoundingClientRect();
    const want = r.top - v.top - v.height * .22;
    return [sc.id ? "#" + sc.id : "main", Math.max(-sc.scrollTop, Math.min(sc.scrollHeight - sc.clientHeight - sc.scrollTop, want))];
  });
  if (Math.abs(by) > 2) await scroll(s, sel, by, ms);
}

async function scroll(s, selector, by, ms = 800) {
  const from = await s.app.evaluate(sel => document.querySelector(sel).scrollTop, selector);
  const n = Math.max(1, Math.round(ms / FRAME));
  for (let i = 1; i <= n; i++) {
    await s.app.evaluate(([sel, v]) => document.querySelector(sel).scrollTop = v, [selector, from + by * ease(i / n)]);
    await frame(s);
  }
}

const zoomed = s => s.page.evaluate(() => camTarget().z !== 1);

async function unzoom(s, ms = 800) {
  if (!(await zoomed(s))) return;
  await tween(s, "cam", { x: 0, y: 0, z: 1 }, ms); await hold(s, ms + 100);
}

// Point the camera at part of the app. If it has to scroll there, zoom out first, scroll, then zoom in,
// so nothing moves under a zoomed-in camera. The element is centered, or, when it's wider than the frame
// at that zoom, aligned to its left edge (anchor "left") or right edge (anchor "right").
async function zoom(s, target, z = 1.5, { dy = 0, anchor = "left", ms = 900 } = {}) {
  const l = loc(s, target);
  await l.waitFor();
  if (!(await inApp(l))) { await unzoom(s); await reveal(s, l); }
  const b = await box(s, l);
  const to = await s.page.evaluate(([b, z, dy, anchor, W, H, CS]) => {
    const c = camAt(now), u = { x: (b.x - c.x) / c.z, y: (b.y - c.y) / c.z, w: b.width / c.z, h: b.height / c.z };   // unzoomed px
    const w = document.querySelector(".win"), L = w.offsetLeft, T = w.offsetTop, R = L + w.offsetWidth, B = T + w.offsetHeight;
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    let x = z * u.w <= W - 120 ? W / 2 - z * (u.x + u.w / 2) : anchor === "right" ? W - 60 - z * (u.x + u.w) : 60 - z * u.x;
    let y = (H - CS) / 2 - z * (u.y + u.h / 2 + dy);
    if (z * (R - L) >= W) x = clamp(x, W - z * R - 36, -z * L + 36);   // a little backdrop past the window's edge is fine
    y = clamp(y, H - CS - z * B - 60, -z * T + 24);   // the window's bottom may rise a little above the caption
    return { x, y, z };
  }, [b, z, dy, anchor, STAGE.width, STAGE.height, CAPTION_SPACE]);
  await tween(s, "cam", to, ms); await hold(s, ms + 150);
}

// A caption at the bottom: a small heading and a line saying what's going on. `text` may hold <em> and <kbd>.
async function caption(s, kicker, text) {
  if (s.capOn) { await tween(s, "cap", 0, 260); await hold(s, 280); }
  await s.page.evaluate(([k, t]) => setCaption(k, t), [kicker, text]);
  await tween(s, "cap", 1, 420); await hold(s, 420);
  s.capOn = true;
}
async function uncaption(s) { if (s.capOn) { await tween(s, "cap", 0, 300); await hold(s, 320); s.capOn = false; } }

// A beat: the caption goes, the camera moves to what the next caption is about, the new caption comes in.
async function beat(s, kicker, text, target, z, opts = {}) {
  await uncaption(s);
  if (target) await zoom(s, target, z, opts); else await unzoom(s);
  await caption(s, kicker, text);
}

// A full-screen card over everything (the opening title and the closing install line).
async function card(s, html, ms, { stay = false, first = false } = {}) {
  await s.page.evaluate(h => setCard(h), html);
  await tween(s, "card", 1, first ? 0 : 600); await hold(s, ms);   // the opening card is up from the first frame
  if (stay) return;   // the video ends on it
  await tween(s, "card", 0, 700); await hold(s, 700);
}

async function moveTo(s, target, { ms = 600, dx = 0, dy = 0 } = {}) {
  const l = loc(s, target);
  await l.waitFor();
  if (!(await inApp(l))) { await unzoom(s); await reveal(s, l); }
  const b = await box(s, l);
  const x = b.x + b.width / 2 + dx, y = b.y + b.height / 2 + dy, { mouse } = s;
  if (x < 0 || x > STAGE.width || y < 0 || y > STAGE.height - 40) console.warn(`  note: ${target} is out of frame`);
  await tween(s, "cur", 1, 200);
  const n = Math.max(1, Math.round(ms / FRAME));
  for (let i = 1; i <= n; i++) {
    const e = ease(i / n), px = mouse.x + (x - mouse.x) * e, py = mouse.y + (y - mouse.y) * e;
    await s.page.mouse.move(px, py);
    await s.page.evaluate(([x, y]) => cursorAt(x, y), [px, py]);
    await frame(s);
  }
  mouse.x = x; mouse.y = y;
}

async function click(s, target, opts) {
  await moveTo(s, target, opts);
  await hold(s, 130);
  await s.page.evaluate(([x, y]) => ring(x, y), [s.mouse.x, s.mouse.y]);
  await s.page.mouse.down(); await s.page.mouse.up();
  await hold(s, 100);
}

const hideCursor = async s => { await tween(s, "cur", 0, 250); await hold(s, 250); };

async function type(s, text, delay = 80) {
  for (const ch of text) { await s.page.keyboard.type(ch); await hold(s, delay + Math.random() * 40); }
}

// Press a key; if the caption shows that key, light it up.
async function key(s, k, wait = 450) {
  await s.page.evaluate(l => hitKey(l), { Enter: "Enter", Escape: "Esc" }[k] ?? k);
  await s.page.keyboard.press(k);
  await hold(s, wait);
}

// Let the app catch up (a search, opening a chat) without the video skipping ahead.
const settle = (s, selector) => s.app.waitForSelector(selector);

const savedRow = (s, name) => s.app.locator(".saved .txt", { hasText: name }).first();

const TITLE = `<div class="logo"><img src="${LOGO}">chatdeck</div><div class="line">Every Claude Code chat, on one screen.</div>`;
const OUTRO = `<div class="logo"><img src="${LOGO}">chatdeck</div>
  <div class="sub">Local · no dependencies · no telemetry · free and open source</div>
  <div class="cmd"><b>$</b> ${INSTALL}</div><div class="sub">then <b style="color:#e6e8ee">chatdeck demo</b> to try it on made-up chats</div>`;

// ---- recording ----------------------------------------------------------------------------------

async function record(s, name, scene, fps = 15, gifWidth = GIF_WIDTH) {
  s.dir = fs.mkdtempSync(path.join(os.tmpdir(), "chatdeck-rec-"));
  s.started = Date.now();
  try {
    await scene(s);
    await hold(s, 500);
  } catch (e) {
    const shot = path.join(os.tmpdir(), `chatdeck-${name}-failed.png`);
    await s.page.screenshot({ path: shot }); console.error(`  failed; the screen at that moment: ${shot}`);
    throw e;
  }
  const mp4 = path.join(MEDIA, `${name}.mp4`), gif = path.join(MEDIA, `${name}.gif`);
  const run = args => execFileSync(ffmpeg, ["-hide_banner", "-loglevel", "error", "-y", ...args], { stdio: "inherit" });
  run(["-framerate", String(FPS), "-i", path.join(s.dir, "%05d.jpg"), "-vf", "scale=1920:1080:flags=lanczos",
       "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4]);
  run(["-i", mp4, "-vf", `fps=${fps},scale=${gifWidth}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];` +
       "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", gif]);
  fs.rmSync(s.dir, { recursive: true, force: true });
  const size = f => (fs.statSync(f).size / 1048576).toFixed(1) + " MB";
  console.log(`  ${path.relative(ROOT, gif)} (${size(gif)}), ${path.relative(ROOT, mp4)} (${size(mp4)}), ${(s.n / FPS).toFixed(0)} s`);
}

// ---- the clips ----------------------------------------------------------------------------------

const CLIPS = {
  // The full walkthrough: for the top of the README and for launch posts.
  async tour(s) {
    await card(s, TITLE, 2400, { first: true });

    await caption(s, "Running now", "Every Claude Code chat that's running, live: what it's doing, how big it is, what it costs");
    await hold(s, 1200);
    await zoom(s, ".card:has-text('stripe-checkout')", 1.25);           // all three running chats
    await moveTo(s, ".card:has-text('stripe-checkout') .name"); await hold(s, 900);
    await moveTo(s, ".card:has-text('android-push') .bar"); await hold(s, 500);
    await caption(s, "Running now", "Chats getting big get a <em>Compact</em> button, so each message costs less");
    await moveTo(s, ".card:has-text('android-push') button:has-text('Compact')"); await hold(s, 1800);

    await beat(s, "Waiting for you", "<em>Amber</em> means a chat is stuck on a permission prompt or a question", ".card.waiting", 1.6);
    await moveTo(s, ".card.waiting .badge"); await hold(s, 1900);
    await beat(s, "Waiting for you", "<em>Open</em> jumps straight to its terminal tab, even on another desktop", ".card.waiting", 1.6, { anchor: "right" });
    await moveTo(s, ".card.waiting button:has-text('Open')"); await hold(s, 2000);

    await beat(s, "Plan limits", "Your 5-hour and weekly limits, and when you'll hit them at this pace", "#usage .lim", 1.3);
    await moveTo(s, "#usage .lim .fc"); await hold(s, 2600);

    await beat(s, "By project", "Group chats by project: what's running and saved in each repo", null);
    await click(s, ".seg button[data-mode=project]"); await hold(s, 500);
    await zoom(s, ".psec", 1.25);
    await moveTo(s, ".psec .phead .pc"); await hold(s, 1400);
    await caption(s, "By project", "Start a <em>New chat</em> in any project with one click");
    await moveTo(s, ".psec .phead button[data-newchat]"); await hold(s, 1900);

    await beat(s, "Search", "Find any old chat by anything that was said in it", null);
    await click(s, ".seg button[data-mode=list]"); await hold(s, 300);
    await click(s, "#q"); await type(s, "upgrade"); await settle(s, ".saved mark"); await hold(s, 600);
    await zoom(s, ".saved:has(mark)", 1.3, { dy: 30 }); await hold(s, 1600);
    await beat(s, "Read", "Read it without resuming it, then resume, rename, export or delete", null);
    await click(s, savedRow(s, "Migrate users")); await settle(s, "#viewer.on .m.you"); await hold(s, 400);
    await zoom(s, "#vBody .m.you", 1.6); await hold(s, 2600);
    await unzoom(s);
    await key(s, "Escape", 400); await key(s, "Escape", 400);

    await hideCursor(s);
    await beat(s, "Keyboard", "Or never touch the mouse: <kbd>/</kbd> search <kbd>j</kbd> <kbd>k</kbd> move <kbd>Enter</kbd> read <kbd>e</kbd> rename <kbd>?</kbd> help",
               ".sub:has-text('Saved')", 1.3, { dy: 140 });
    for (const k of ["j", "j", "j", "k"]) await key(s, k, 450);
    await unzoom(s);
    await key(s, "Enter", 1500); await key(s, "Escape", 700);

    await beat(s, "Themes", "Light and dark, or follow your system", null);
    await click(s, "#themeBtn"); await hold(s, 2200);                 // auto -> light
    await click(s, "#themeBtn"); await hold(s, 1100);                 // -> dark
    await uncaption(s); await hideCursor(s);

    await card(s, OUTRO, 4200, { stay: true });
  },

  // The tour cut to about 30 seconds, for X and LinkedIn: the four things that sell it.
  async teaser(s) {
    await card(s, TITLE, 1800, { first: true });

    await beat(s, "Waiting for you", "See which Claude Code chat is <em>waiting for you</em>, and jump straight to it", ".card.waiting", 1.6);
    await moveTo(s, ".card.waiting .badge"); await hold(s, 1300);
    await zoom(s, ".card.waiting", 1.6, { anchor: "right", ms: 700 });
    await moveTo(s, ".card.waiting button:has-text('Open')"); await hold(s, 1100);   // not a click: the demo would say it can't open terminals

    await beat(s, "Plan limits", "Know when you'll hit your 5-hour and weekly limits", "#usage .lim", 1.3);
    await moveTo(s, "#usage .lim .fc"); await hold(s, 1700);

    await beat(s, "Search", "Find any old chat by anything that was said in it", null);
    await click(s, "#q"); await type(s, "upgrade", 60); await settle(s, ".saved mark"); await hold(s, 300);
    await zoom(s, ".saved:has(mark)", 1.3, { dy: 30 }); await hold(s, 900);
    await click(s, savedRow(s, "Migrate users")); await settle(s, "#viewer.on .m.you"); await hold(s, 200);
    await zoom(s, "#vBody .m.you", 1.6); await hold(s, 1500);
    await unzoom(s, 600);
    await key(s, "Escape", 300); await key(s, "Escape", 300);

    await beat(s, "By project", "Every project in one place, with a <em>New chat</em> button for each", null);
    await click(s, ".seg button[data-mode=project]"); await hold(s, 300);
    await zoom(s, ".psec", 1.25);
    await moveTo(s, ".psec .phead button[data-newchat]"); await hold(s, 1400);
    await uncaption(s); await hideCursor(s);

    await card(s, OUTRO, 3600, { stay: true });
  },

  // Find a chat from months ago by anything that was said in it.
  async search(s) {
    await caption(s, "Search", "Search every message in every chat, not just the names"); await hold(s, 600);
    await click(s, "#q"); await type(s, "upgrade"); await settle(s, ".saved mark"); await hold(s, 600);
    await zoom(s, ".sub:has-text('Saved')", 1.45, { dy: 70 }); await hold(s, 1500);
    await beat(s, "Read", "Open one to read it without resuming it", ".saved:has-text('React Native')", 1.45);
    await click(s, savedRow(s, "React Native")); await settle(s, "#viewer.on .m.you"); await hold(s, 300);
    await zoom(s, "#vBody .m.you", 1.6); await hold(s, 1600);
    await beat(s, "Read", "Hide the tool calls to see just the conversation", "#viewer header", 1.4);
    await click(s, "#vTools"); await hold(s, 1300);
    await click(s, "#vTools"); await hold(s, 700);
    await caption(s, "Read", "<em>Resume</em> it in a terminal, or <em>Export .md</em> to keep it");
    await moveTo(s, "#vResume"); await hold(s, 1000);
    await moveTo(s, "#vExport"); await hold(s, 1300);
    await uncaption(s); await unzoom(s);
    await click(s, "#vBack"); await hold(s, 800);
  },

  // Every project in one place, with what's running and waiting in each.
  async projects(s) {
    await caption(s, "By project", "One section per project, with its worktrees and subfolders"); await hold(s, 400);
    await click(s, ".seg button[data-mode=project]"); await hold(s, 400);
    await zoom(s, ".psec", 1.25); await hold(s, 1200);
    await caption(s, "By project", "See what's <em>waiting</em>, running and saved in each one at a glance");
    await moveTo(s, ".psec .phead .pc"); await hold(s, 1900);
    await caption(s, "By project", "Start a <em>New chat</em> right in that project");
    await moveTo(s, ".psec .phead button[data-newchat]"); await hold(s, 1800);
    await beat(s, "By project", "Fold away the ones you're not working on", null);
    for (const p of ["api-server", "mobile-app", "mobile-app", "api-server"]) {
      await click(s, `.phead:has-text('${p}') .pn`); await hold(s, 800);
    }
    await uncaption(s);
  },

  // Driving it from the keyboard: j/k, Enter, e to rename, ? for help.
  async keyboard(s) {
    await s.app.locator("#q").focus(); await s.page.keyboard.press("Escape");   // put the keyboard in the app
    await beat(s, "Keyboard", "Move through saved chats with <kbd>j</kbd> <kbd>k</kbd>", ".sub:has-text('Saved')", 1.3, { dy: 140 });
    for (const k of ["j", "j", "j"]) await key(s, k, 550);
    await beat(s, "Keyboard", "<kbd>Enter</kbd> reads it, <kbd>Esc</kbd> comes back", null);
    await key(s, "Enter", 1500);
    await key(s, "Escape", 600);
    await beat(s, "Keyboard", "<kbd>e</kbd> gives it a name you'll recognise later", ".saved.cur", 1.6);
    await key(s, "e", 500);
    await s.page.keyboard.press("Control+A"); await type(s, "Postgres 16 runbook", 70);
    await key(s, "Enter", 1500);
    await beat(s, "Keyboard", "<kbd>?</kbd> shows every shortcut", null);
    await key(s, "?", 300);
    await zoom(s, "#help", 1.5); await hold(s, 1800);
    await unzoom(s); await key(s, "?", 400);
    await uncaption(s);
  },
};

// ---- screenshots --------------------------------------------------------------------------------

async function shots(browser) {
  const shot = async (page, file, target) => {
    const out = path.join(file.includes("/") ? DOCS : MEDIA, file);
    await (target ? page.locator(target).first() : page).screenshot({ path: out });
    console.log("  " + path.relative(ROOT, out));
  };
  let page = await newPage(browser, { width: 1280, height: 860 });
  await shot(page, "./dashboard.png");
  await shot(page, "usage.png", "#usage");
  await page.evaluate(() => document.querySelector("main").scrollTop = 0);
  const running = page.locator(".sub", { hasText: "Running now" });
  await running.scrollIntoViewIfNeeded();
  const box = async s => page.locator(s).first().boundingBox();
  const a = await running.boundingBox(), b = await box(".sub:has-text('Saved')");
  await page.screenshot({ path: path.join(MEDIA, "running.png"), clip: { x: 0, y: a.y - 12, width: 1280, height: b.y - a.y + 4 } });
  console.log("  docs/media/running.png");

  await page.evaluate(() => document.querySelector("main").scrollTop = 0);
  await page.locator("#q").fill("upgrade"); await page.waitForSelector(".saved mark"); await sleep(300);
  await page.locator(".chatsHead").evaluate(e => e.scrollIntoView());
  await shot(page, "search.png");
  await page.locator("#q").fill(""); await sleep(300);

  await page.click(".seg button[data-mode=project]"); await sleep(500);
  await page.locator(".chatsHead").evaluate(e => e.scrollIntoView());
  await shot(page, "./by-project.png");
  await page.click(".seg button[data-mode=list]"); await sleep(300);

  await page.locator(".saved .txt", { hasText: "Postgres" }).click(); await sleep(700);
  await page.screenshot({ path: path.join(DOCS, "viewer.png"), clip: { x: 0, y: 0, width: 1280, height: 520 } });   // the demo chats are short
  console.log("  docs/viewer.png");
  await page.keyboard.press("Escape"); await sleep(300);

  await page.keyboard.press("?"); await sleep(300);
  await shot(page, "help.png", "#help");
  await page.keyboard.press("?");
  await page.context().close();

  // a narrower window for the social card, so its text stays readable at that size
  page = await newPage(browser, { width: 940, height: 640 });
  await page.screenshot({ path: path.join(os.tmpdir(), "chatdeck-card.png") });
  await page.context().close();

  page = await newPage(browser, { width: 1280, height: 860, theme: "light" });
  await shot(page, "./light.png");
  await page.context().close();
}

// A screenshot on a backdrop with a window title bar, for posts and the release page.
async function framed(browser) {
  const img = f => "data:image/png;base64," + fs.readFileSync(path.isAbsolute(f) ? f : path.join(DOCS, f)).toString("base64");
  const logo = "data:image/svg+xml;base64," + fs.readFileSync(path.join(ROOT, "src/chatdeck/static/logo.svg")).toString("base64");
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 640 }, deviceScaleFactor: 2 })).newPage();
  const base = `<style>${FONT_CSS_INLINE}*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",system-ui,sans-serif;color:#e6e8ee;
    background:radial-gradient(1200px 700px at 85% -10%,#2b4a86 0%,transparent 60%),radial-gradient(900px 600px at -10% 110%,#1f5a46 0%,transparent 55%),#0b0d12}
    .win{border-radius:12px;overflow:hidden;box-shadow:0 30px 80px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.08);background:#0f1115}
    .bar{height:30px;background:#1a1e26;display:flex;align-items:center;gap:7px;padding:0 12px}.bar i{width:11px;height:11px;border-radius:50%}
    .win img{display:block;width:100%}</style>`;
  const win = src => `<div class="win"><div class="bar"><i style="background:#f06a6a"></i><i style="background:#f5b942"></i><i style="background:#3ecf8e"></i></div><img src="${src}"></div>`;

  // GitHub's social preview size: 1280x640.
  await page.setContent(`${base}<div style="display:flex;height:640px;align-items:center;padding:0 0 0 64px;gap:44px;overflow:hidden">
    <div style="flex:0 0 400px"><div style="display:flex;align-items:center;gap:14px"><img src="${logo}" width="52" height="52">
      <span style="font-size:46px;font-weight:700;letter-spacing:-.5px">chatdeck</span></div>
      <div style="font-size:30px;line-height:1.25;font-weight:600;margin:26px 0 18px">Every Claude Code chat,<br>on one screen.</div>
      <div style="font-size:19px;line-height:1.5;color:#a9b0be">See what's running and what's waiting for you. Search old chats.
        Watch your plan limits. Local, no dependencies.</div>
      <div style="margin-top:26px;font:17px Consolas,monospace;background:#171a21;border:1px solid #262a33;border-radius:8px;padding:10px 14px;display:inline-block">
        <span style="color:#8b92a1">$</span> pipx install chatdeck</div></div>
    <div style="flex:1;transform:translateY(70px);min-width:780px">${win(img(path.join(os.tmpdir(), "chatdeck-card.png")))}</div></div>`);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: path.join(MEDIA, "social-card.png") });
  console.log("  docs/media/social-card.png");

  await page.setViewportSize({ width: 1280, height: 900 });
  for (const f of ["dashboard", "by-project", "viewer", "light"]) {
    await page.setContent(`${base}<div style="padding:56px 72px">${win(img(f + ".png"))}</div>`);
    await page.evaluate(() => document.fonts.ready);
    await page.locator("body > div").screenshot({ path: path.join(MEDIA, `${f}-framed.png`) });
    console.log(`  docs/media/${f}-framed.png`);
  }
  await page.context().close();
}

// ---- main ---------------------------------------------------------------------------------------

const want = process.argv.slice(2);
const pick = k => !want.length || want.includes(k);
fs.mkdirSync(MEDIA, { recursive: true });
const browser = await chromium.launch();
try {
  if (pick("shots")) {
    console.log("screenshots");
    const srv = await startDemo();
    try { await shots(browser); } finally { await stopDemo(srv); }
    await framed(browser);
  }
  for (const [name, scene] of Object.entries(CLIPS)) {
    if (!pick(name)) continue;
    console.log(name);
    const srv = await startDemo();   // a fresh demo each time, so renames from one clip don't leak into the next
    try {
      const s = await newStage(browser);
      await record(s, name, scene, ...(["tour", "teaser"].includes(name) ? [8, 800] : [12]));   // keep the long one's GIF small
      await s.page.context().close();
    } finally { await stopDemo(srv); }
  }
} finally {
  await browser.close();
}
