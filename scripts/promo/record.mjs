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
const VIDEO = { width: 1200, height: 750 };   // CSS pixels; recorded at 2x, GIFs scaled to GIF_WIDTH
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

// ---- the page: fonts, a visible cursor -----------------------------------------------------------

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

// Screen recordings don't show the mouse, so draw one; it follows the real (Playwright) mouse.
const CURSOR = `(() => {
  const css = ${JSON.stringify(FONT_CSS)} + \`
    #__cur{position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;opacity:0;transition:opacity .2s;
      filter:drop-shadow(0 1px 2px rgba(0,0,0,.45))}
    #__cur.on{opacity:1}
    .__ring{position:fixed;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:2px solid #7fb0ff;
      z-index:2147483646;pointer-events:none;animation:__ring .45s ease-out forwards}
    @keyframes __ring{from{transform:scale(.3);opacity:1}to{transform:scale(1.2);opacity:0}}\`;
  addEventListener("DOMContentLoaded", () => {
    const st = document.createElement("style"); st.textContent = css; document.head.append(st);
    if (!window.__cursor) return;
    const c = document.createElement("div"); c.id = "__cur";
    c.innerHTML = '<svg viewBox="0 0 22 22" width="22" height="22"><path d="M3 2l14 9.5-6.2 1.1 3.6 7.1-2.6 1.3-3.6-7.2L3 18z" fill="#fff" stroke="#111" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    document.body.append(c);
    addEventListener("mousemove", e => { c.style.transform = \`translate(\${e.clientX - 3}px,\${e.clientY - 2}px)\`; c.classList.add("on") }, true);
    addEventListener("mousedown", e => { const r = document.createElement("div"); r.className = "__ring";
      r.style.left = e.clientX + "px"; r.style.top = e.clientY + "px"; document.body.append(r); setTimeout(() => r.remove(), 500) }, true);
  });
})()`;

async function newPage(browser, { width, height, scale = 2, theme = "dark", cursor = false }) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: scale, colorScheme: theme });
  await ctx.route("**/__font/*", r => r.fulfill({ path: path.join(WINFONTS, path.basename(new URL(r.request().url()).pathname)) }));
  if (cursor) await ctx.addInitScript("window.__cursor = true");
  await ctx.addInitScript(CURSOR);
  const page = await ctx.newPage();
  await page.goto(URL_);
  await page.waitForSelector("#list .card");
  await page.evaluate(() => document.fonts.ready);
  await sleep(400);
  return page;
}

// ---- moving like a person ----------------------------------------------------------------------

const mouse = { x: 600, y: 500 };

async function moveTo(page, target, { steps = 28, dx = 0, dy = 0 } = {}) {
  const loc = typeof target === "string" ? page.locator(target).first() : target;
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  const x = b.x + b.width / 2 + dx, y = b.y + b.height / 2 + dy;
  // ease in and out along the way, instead of Playwright's straight constant-speed line
  for (let i = 1; i <= steps; i++) {
    const t = i / steps, e = t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
    await page.mouse.move(mouse.x + (x - mouse.x) * e, mouse.y + (y - mouse.y) * e);
    await sleep(12);
  }
  mouse.x = x; mouse.y = y;
}

async function click(page, target, opts) {
  await moveTo(page, target, opts);
  await sleep(180);
  await page.mouse.down(); await sleep(70); await page.mouse.up();
}

async function type(page, text, delay = 85) {
  for (const ch of text) { await page.keyboard.type(ch); await sleep(delay + Math.random() * 40); }
}

async function key(page, k, wait = 450) { await page.keyboard.press(k); await sleep(wait); }

async function scroll(page, selector, by, ms = 900) {
  await page.evaluate(([s, by, ms]) => new Promise(r => {
    const el = s ? document.querySelector(s) : document.scrollingElement;
    const from = el.scrollTop, t0 = performance.now();
    const step = now => { const t = Math.min(1, (now - t0) / ms), e = t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      el.scrollTop = from + by * e; t < 1 ? requestAnimationFrame(step) : r() };
    requestAnimationFrame(step);
  }), [selector, by, ms]);
}

const savedRow = (page, name) => page.locator(".saved .txt", { hasText: name }).first();

// ---- recording ----------------------------------------------------------------------------------

// Chrome's screencast sends a frame whenever the page repaints; each frame is held until the next one.
async function record(page, name, scene, fps = 15) {
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  cdp.on("Page.screencastFrame", f => {
    frames.push({ data: Buffer.from(f.data, "base64"), t: f.metadata.timestamp });
    cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  });
  const { width, height } = page.viewportSize();
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 95, maxWidth: width * 2, maxHeight: height * 2 });
  await sleep(300);
  await scene();
  await sleep(600);
  await cdp.send("Page.stopScreencast");
  const end = Date.now() / 1000;

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "chatdeck-rec-"));
  const list = [];
  frames.forEach((f, i) => {
    const file = path.join(dir, `${String(i).padStart(5, "0")}.jpg`);
    fs.writeFileSync(file, f.data);
    list.push(`file '${file}'`, `duration ${((frames[i + 1]?.t ?? end) - f.t).toFixed(4)}`);
  });
  list.push(list.at(-2));   // concat needs the last file repeated for its duration to count
  fs.writeFileSync(path.join(dir, "list.txt"), list.join("\n"));

  const mp4 = path.join(MEDIA, `${name}.mp4`), gif = path.join(MEDIA, `${name}.gif`);
  const run = args => execFileSync(ffmpeg, ["-hide_banner", "-loglevel", "error", "-y", ...args], { stdio: "inherit" });
  run(["-f", "concat", "-safe", "0", "-i", path.join(dir, "list.txt"), "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2",
       "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4]);
  run(["-i", mp4, "-vf", `fps=${fps},scale=${GIF_WIDTH}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];` +
       "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", gif]);
  fs.rmSync(dir, { recursive: true, force: true });
  const size = f => (fs.statSync(f).size / 1048576).toFixed(1) + " MB";
  console.log(`  ${path.relative(ROOT, gif)} (${size(gif)}), ${path.relative(ROOT, mp4)} (${size(mp4)})`);
}

// ---- the clips ----------------------------------------------------------------------------------

const CLIPS = {
  // The 25-second overview for the top of the README and for posts.
  async tour(page) {
    await sleep(1600);                                                  // the amber "waiting for you" chat pulses
    await moveTo(page, ".card.waiting .badge"); await sleep(1200);
    await moveTo(page, ".card.waiting button:has-text('Open')"); await sleep(900);
    await click(page, ".seg button[data-mode=project]"); await sleep(1300);
    await scroll(page, "main", 420, 1400); await sleep(900);
    await moveTo(page, ".phead button[data-newchat]"); await sleep(900);
    await scroll(page, "main", -420, 900);
    await click(page, ".seg button[data-mode=list]"); await sleep(500);
    await click(page, "#q"); await type(page, "postgres"); await sleep(1300);
    await click(page, savedRow(page, "Migrate users")); await sleep(1400);
    await scroll(page, "#vBody", 300, 1400); await sleep(1000);
    await key(page, "Escape", 600); await key(page, "Escape", 600);
    await click(page, "#themeBtn"); await sleep(1300);                 // auto -> light
    await click(page, "#themeBtn"); await sleep(900);                  // -> dark
  },

  // Find a chat from months ago by anything that was said in it.
  async search(page) {
    await sleep(700);
    await click(page, "#q"); await type(page, "upgrade"); await sleep(1500);   // matches what was said, not just names
    await click(page, savedRow(page, "React Native")); await sleep(1600);
    await scroll(page, "#vBody", 250, 1000); await sleep(800);
    await click(page, "#vTools"); await sleep(1000);                   // hide the tool calls
    await click(page, "#vTools"); await sleep(700);
    await click(page, "#vBack"); await sleep(500);
    await key(page, "Escape", 300); await page.locator("#q").fill(""); await sleep(400);
  },

  // Every project in one place, with what's running and waiting in each.
  async projects(page) {
    await sleep(600);
    await click(page, ".seg button[data-mode=project]"); await sleep(1300);
    await scroll(page, "main", 400, 1200); await sleep(700);
    await click(page, ".phead:has-text('webshop')", { dx: -120 }); await sleep(900);   // collapse
    await click(page, ".phead:has-text('webshop')", { dx: -120 }); await sleep(900);   // and open again
    await moveTo(page, ".phead:has-text('api-server') button[data-newchat]"); await sleep(1300);
    await click(page, ".seg button[data-mode=list]"); await sleep(500);
  },

  // Driving it from the keyboard: j/k, Enter, e to rename, ? for help.
  async keyboard(page) {
    await sleep(600);
    await page.locator(".sub", { hasText: "Saved" }).scrollIntoViewIfNeeded();
    await page.evaluate(() => document.getElementById("__cur").remove());   // keyboard only
    for (const k of ["j", "j", "j"]) await key(page, k, 500);
    await key(page, "Enter", 1500);
    await scroll(page, "#vBody", 220, 900); await sleep(500);
    await key(page, "Escape", 700);
    await key(page, "e", 500);
    await page.keyboard.press("Control+A"); await type(page, "Postgres 16 runbook", 70);
    await key(page, "Enter", 1300);
    await key(page, "?", 1800); await key(page, "?", 500);
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
      const page = await newPage(browser, { ...VIDEO, cursor: true });
      mouse.x = VIDEO.width * .6; mouse.y = VIDEO.height * .55;
      await record(page, name, () => scene(page), name === "tour" ? 10 : 15);   // keep the long one's GIF small
      await page.context().close();
    } finally { await stopDemo(srv); }
  }
} finally {
  await browser.close();
}
