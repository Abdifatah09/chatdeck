// chatdeck site: copy key, feature tabs, and the hero's ASCII field.
(() => {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');

  // Copy key
  document.querySelectorAll('[data-copy]').forEach((btn) => {
    const src = document.getElementById(btn.dataset.copy);
    const status = document.querySelector('.copied');
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(src.textContent.trim());
        btn.textContent = 'Copied';
        if (status) status.textContent = 'Install command copied.';
      } catch {
        if (status) status.textContent = 'Could not copy. Select the command instead.';
      }
      setTimeout(() => { btn.textContent = 'Copy'; if (status) status.textContent = ''; }, 2000);
    });
  });

  // Feature tabs: one screen at a time, arrow keys move between tabs
  document.querySelectorAll('[data-tabs]').forEach((root) => {
    const tabs = [...root.querySelectorAll('[role="tab"]')];
    const pick = (t, focus) => {
      tabs.forEach((x) => {
        const on = x === t;
        x.setAttribute('aria-selected', on); x.tabIndex = on ? 0 : -1;
        document.getElementById(x.getAttribute('aria-controls')).hidden = !on;
      });
      if (focus) t.focus();
    };
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => pick(t));
      t.addEventListener('keydown', (e) => {
        const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (d) { e.preventDefault(); pick(tabs[(i + d + tabs.length) % tabs.length], true); }
      });
    });
    pick(tabs[0]);
  });

  // The demo frame shows a focus ring while focus is inside it
  const live = document.querySelector('.live');
  if (live) {
    const mark = () => live.classList.toggle('is-focus', document.activeElement === live.querySelector('iframe'));
    addEventListener('blur', () => setTimeout(mark)); addEventListener('focus', mark); document.addEventListener('focusin', mark);
  }

  // Hero simulation: four Claude Code panes; one stops to ask, chatdeck flags it, Open jumps to it
  const sim = document.querySelector('[data-sim]');
  if (sim) {
    const pane = (k) => sim.querySelector(`[data-pane="${k}"]`);
    const body = (k) => pane(k).querySelector('.pane__body');
    const row = (k) => sim.querySelector(`[data-row="${k}"]`);
    const sum = sim.querySelector('[data-sum]');
    const ROW = { api: ['Working', 0.06, '24k'], shop: ['Working', 0.1, '41k'], app: ['Working', 0.3, '120k'], blog: ['Working', 0.02, '8k'] };
    let t = 0, events = [], waiting = false, opened = false, running = false, held = reduce.matches, seen = false, gen = 0;

    const setRow = (k, status, w, tok, cls = '') => {
      const r = row(k);
      r.className = 'row' + (cls ? ' ' + cls : '');
      r.querySelector('.row__status').textContent = status;
      r.querySelector('.row__bar i').style.setProperty('--w', w);
      r.querySelector('.row__tok').textContent = tok;
    };
    const summary = () => {
      const w = sim.querySelectorAll('.row.is-waiting').length;
      sum.textContent = (w ? w + ' waiting for you, ' : '') + '4 running';
    };
    const line = (k, text, cls) => {
      const b = body(k), el = document.createElement('span');
      el.className = cls; el.textContent = text; b.append(el);
      while (b.children.length > 6) b.firstElementChild.remove();
      return el;
    };
    const type = (k, text, at, cls = 'l-you') => {
      let el;
      at(0, () => { el = line(k, '', cls + ' l-typing'); });
      for (let i = 1; i <= text.length; i++) at(i * 28, () => { el.textContent = text.slice(0, i); });
      at(text.length * 28 + 60, () => el.classList.remove('l-typing'));
      return text.length * 28 + 60;
    };

    const plan = () => {
      events = [];
      const at = (ms) => (d, fn) => events.push({ at: ms + d, fn });
      // api-server: asks for permission at about 4.2s
      let a = type('api', '> Add rate limiting to the login route', at(300));
      events.push({ at: 300 + a + 500, fn: () => line('api', 'Read src/routes/auth.ts', 'l-tool') });
      events.push({ at: 300 + a + 1300, fn: () => { line('api', 'Write src/middleware/rateLimit.ts (+41)', 'l-tool'); setRow('api', 'Working', 0.18, '72k'); } });
      events.push({ at: 300 + a + 2200, fn: () => { line('api', 'Bash wants to run: npm test', 'l-ask'); line('api', 'Allow? (y/n)', 'l-ask'); pane('api').classList.add('is-waiting'); setRow('api', 'Waiting for you', 0.24, '96k', 'is-waiting'); waiting = true; summary(); } });
      events.push({ at: 300 + a + 5200, fn: () => !reduce.matches && openChat('api') });  // nobody clicked: chatdeck's Open does it
      // webshop: keeps working
      let b = type('shop', '> Add Stripe checkout to the cart page', at(900));
      [[700, 'Read src/pages/cart.tsx', 'l-tool', 0.16, '62k'], [1800, 'Edit src/api/checkout.ts (+38 -2)', 'l-tool', 0.24, '98k'], [3200, 'Bash npm run build', 'l-tool', 0.3, '121k'], [4600, 'built in 4.1s', 'l-done', 0.33, '133k'], [6200, 'Adding the Pay button…', 'l-out', 0.36, '142k']]
        .forEach(([d, txt, c, w, tok]) => events.push({ at: 900 + b + d, fn: () => { line('shop', txt, c); setRow('shop', 'Working', w, tok); } }));
      // mobile-app: the context keeps growing until it is getting big
      let c = type('app', '> Fix push notifications on Android 14', at(1500));
      events.push({ at: 1500 + c + 800, fn: () => { line('app', 'Read 14 files', 'l-tool'); setRow('app', 'Working', 0.36, '144k'); } });
      events.push({ at: 1500 + c + 2400, fn: () => { line('app', 'Thinking…', 'l-out'); setRow('app', 'Working', 0.41, '165k'); } });
      events.push({ at: 1500 + c + 4600, fn: () => { line('app', 'Edit android/app/src/main/AndroidManifest.xml', 'l-tool'); setRow('app', 'Getting big', 0.47, '188k', 'is-big'); } });
      // blog: finishes and goes idle
      let d = type('blog', '> Draft a post about the 0.2.1 release', at(2000));
      events.push({ at: 2000 + d + 900, fn: () => { line('blog', 'Write posts/0-2-1.md', 'l-tool'); setRow('blog', 'Working', 0.07, '29k'); } });
      events.push({ at: 2000 + d + 2200, fn: () => { line('blog', 'Done. The draft is in posts/0-2-1.md', 'l-done'); setRow('blog', 'Idle', 0.08, '31k', 'is-idle'); } });
      events.push({ at: 15500, fn: () => sim.classList.add('is-fading') });
      events.push({ at: 16100, fn: reset });
      events.sort((x, y) => x.at - y.at);
    };

    function openChat(k) {
      sim.querySelectorAll('.pane').forEach((p) => p.classList.toggle('is-focus', p === pane(k)));
      sim.querySelectorAll('.row').forEach((r) => r.classList.toggle('is-focus', r === row(k) && !r.classList.contains('is-waiting')));
      if (k !== 'api' || !waiting || opened) return;
      opened = true; waiting = false;
      const now = t, g = gen;
      const later = (d, fn) => events.push({ at: now + d, fn: () => g === gen && fn() });
      later(350, () => { const last = body('api').lastElementChild; last.textContent = 'Allow? (y/n) y'; pane('api').classList.remove('is-waiting'); setRow('api', 'Working', 0.25, '97k', 'is-focus'); summary(); });
      later(1300, () => line('api', '42 passing (3.1s)', 'l-done'));
      later(2300, () => { line('api', 'Done. Logins are limited to 5 tries a minute.', 'l-done'); setRow('api', 'Idle', 0.26, '101k', 'is-idle'); pane('api').classList.remove('is-focus'); });
      events.sort((x, y) => x.at - y.at);
      if (held) flush(Infinity);
    }

    function reset() {
      gen++; t = 0; waiting = false; opened = false;
      sim.classList.remove('is-fading');
      sim.querySelectorAll('.pane').forEach((p) => p.classList.remove('is-waiting', 'is-focus'));
      ['api', 'shop', 'app', 'blog'].forEach((k) => { body(k).textContent = ''; setRow(k, ...ROW[k]); });
      summary(); plan();
    }
    // Run what is due; stop if an event restarted the loop, so the new round starts from its own zero
    const flush = (until) => { const g = gen; while (events.length && events[0].at <= until && g === gen) { const e = events.shift(); t = Math.max(t, e.at); e.fn(); } };

    let last = 0;
    const tick = (now) => {
      if (!running) return;
      const dt = Math.min(100, now - (last || now)); last = now;
      t += dt; flush(t);
      requestAnimationFrame(tick);
    };
    const sync = () => {
      const run = seen && !held;
      if (run && !running) { running = true; last = 0; requestAnimationFrame(tick); }
      if (!run) running = false;
      btn.textContent = held ? 'Play' : 'Pause';
    };
    const btn = sim.querySelector('[data-loop-toggle]');
    btn.addEventListener('click', () => { held = !held; sync(); });
    sim.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => openChat(b.dataset.open)));
    new IntersectionObserver(([e]) => { seen = e.isIntersecting; sync(); }).observe(sim);

    reset();
    if (reduce.matches) { flush(9000); events = []; }  // one composed still: the chat waiting for you
    sync();
  }

  // Limit calculator: when you hit the 5-hour limit at this pace
  const pace = document.querySelector('[data-pace]');
  if (pace) {
    const res = document.querySelector('.pace__result');
    const used = pace.querySelector('#pace-used'), reset = pace.querySelector('#pace-reset');
    const fmt = (m) => { m = Math.max(0, Math.round(m)); const h = Math.floor(m / 60), r = m % 60; return h ? `${h}h ${r}m` : `${r}m`; };
    const calc = () => {
      const u = +used.value, left = +reset.value, rate = +pace.querySelector('[name="pace"]:checked').value; // percent per hour
      const eta = (100 - u) / rate * 60, proj = u + rate * left / 60;
      pace.querySelector('[data-out="used"]').textContent = u + '%';
      pace.querySelector('[data-out="reset"]').textContent = fmt(left);
      const fig = res.querySelector('[data-figure]'), label = res.querySelector('[data-verdict-label]'), verdict = res.querySelector('[data-verdict]');
      if (eta < left) {
        label.textContent = 'At this pace you hit the limit in';
        fig.textContent = fmt(eta); fig.classList.add('is-warn');
        verdict.textContent = `That is ${fmt(left - eta)} before it resets. chatdeck shows this for your real limits, live.`;
      } else {
        label.textContent = 'At this pace you reach the reset with';
        fig.textContent = Math.round(100 - proj) + '% left'; fig.classList.remove('is-warn');
        verdict.textContent = `It resets in ${fmt(left)}, before you run out. chatdeck shows this for your real limits, live.`;
      }
      const cells = 50, cu = Math.round(u / 2), cp = Math.min(cells, Math.round(proj / 2));
      const meter = res.querySelector('[data-meter]'); meter.textContent = '';
      const run = (n, cls, ch) => { if (n <= 0) return; const sp = document.createElement('span'); sp.className = cls; sp.textContent = ch.repeat(n); meter.append(sp); };
      run(cu, 'c-used', '#'); run(cp - cu, 'c-proj', '='); run(cells - cp, 'c-free', '.');
      const over = Math.round((proj - 100) / 2); run(Math.min(12, over), 'c-over', '>');
    };
    pace.addEventListener('input', calc); pace.addEventListener('submit', (e) => e.preventDefault());
    calc();
  }

  // ASCII field: raw transcript noise on the left resolves into chat rows on the right
  const canvas = document.querySelector('.field');
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const css = getComputedStyle(document.documentElement);
  const tint = ['--color-glyph-1', '--color-glyph-2', '--color-glyph-3', '--color-glyph-4', '--color-accent-deep'].map((v) => css.getPropertyValue(v).trim());
  const ramp = ' .:-=+*#%@';
  const CW = 8, CH = 16;
  let w = 0, h = 0, cols = 0, rows = 0, dpr = 1, running = false, last = 0, t0 = performance.now();
  const lens = { x: -999, y: -999 };

  const size = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth; h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = '500 12px "JetBrains Mono", ui-monospace, monospace';
    ctx.textBaseline = 'top';
    cols = Math.ceil(w / CW); rows = Math.ceil(h / CH);
  };

  const smooth = (a, b, x) => { const k = Math.min(1, Math.max(0, (x - a) / (b - a))); return k * k * (3 - 2 * k); };
  const hash = (x, y) => { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); };

  const draw = (now) => {
    const t = (now - t0) / 1000;
    ctx.clearRect(0, 0, w, h);
    const narrow = w < 960;
    const left = narrow ? 0.0 : 0.56;
    for (let r = 0; r < rows; r++) {
      const band = r % 3 === 1;             // every third row is a chat row once resolved
      const rowLen = 0.55 + 0.35 * hash(r, 7);
      const waiting = r === Math.floor(rows * 0.42) - (Math.floor(rows * 0.42) % 3) + 1;
      for (let c = 0; c < cols; c++) {
        const u = c / cols;
        const fade = narrow ? 0.35 : smooth(left - 0.04, left + 0.14, u);
        if (fade <= 0.01) continue;
        // raw: drifting noise
        const raw = 0.5 + 0.5 * Math.sin(c * 0.21 + t * 1.3 + Math.sin(r * 0.37 + t * 0.6) * 2.2) * Math.cos(r * 0.29 - t * 0.8 + c * 0.05);
        // resolved: tidy rows, filled from the left of their zone
        const zone = smooth(0.62, 0.86, u);
        const rowFill = band && u < 0.62 + rowLen * 0.38 ? 0.75 + 0.2 * hash(c, r) : 0.06;
        let v = raw * (1 - zone) * 0.85 + rowFill * zone;
        const dx = c * CW - lens.x, dy = r * CH - lens.y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < 90) v += 0.3 * (1 - d / 90);
        v *= fade;
        if (v < 0.12) continue;
        const ch = ramp[Math.min(ramp.length - 1, Math.floor(v * ramp.length))];
        let tone = Math.min(3, Math.floor(v * 4));
        if (waiting && zone > 0.5 && rowFill > 0.5) tone = 4;
        ctx.fillStyle = tint[tone];
        ctx.fillText(ch, c * CW, r * CH + 2);
      }
    }
  };

  const loop = (now) => {
    if (!running) return;
    if (now - last > 33) { draw(now); last = now; }
    requestAnimationFrame(loop);
  };
  const start = () => { if (running || reduce.matches) return; running = true; requestAnimationFrame(loop); };
  const stop = () => { running = false; };

  size();
  draw(t0 + 4000);
  new IntersectionObserver(([e]) => (e.isIntersecting ? start() : stop())).observe(canvas);
  addEventListener('resize', () => { size(); draw(performance.now()); });
  reduce.addEventListener('change', () => { if (reduce.matches) { stop(); draw(t0 + 4000); } else start(); });
  const hero = canvas.parentElement;
  hero.addEventListener('pointermove', (e) => { const b = canvas.getBoundingClientRect(); lens.x = e.clientX - b.left; lens.y = e.clientY - b.top; });
  hero.addEventListener('pointerleave', () => { lens.x = lens.y = -999; });
  document.fonts && document.fonts.ready.then(() => { size(); draw(performance.now()); });
})();
