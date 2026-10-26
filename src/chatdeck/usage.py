"""Token usage from transcripts, plan limits from saved status-line payloads, and a forecast
of when each limit runs out at the current pace."""
import datetime, glob, json, os, threading, time

WINDOWS = {"five_hour": ("5-hour limit", 90 * 60), "seven_day": ("Weekly limit", 12 * 3600)}  # key -> (label, lookback secs)


def _secs(t):
    if isinstance(t, (int, float)): return t / 1000 if t > 1e12 else t
    try: return datetime.datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
    except Exception: return None


class LimitHistory:
    """Samples of (time, window, used %, reset time), kept in memory and in a small JSONL file
    so the forecast survives restarts."""

    def __init__(self, path, keep=8 * 86400):
        self.path = path; self.keep = keep; self.samples = []; self.lock = threading.Lock()
        try:
            with open(path) as f:
                self.samples = [json.loads(l) for l in f if l.strip()]
        except (FileNotFoundError, ValueError):
            pass

    def add(self, at, data):
        new = []
        with self.lock:
            for key in WINDOWS:
                o = data.get(key) or {}
                pct = o.get("used_percentage"); reset = _secs(o.get("resets_at"))
                if pct is None or reset is None: continue
                last = next((s for s in reversed(self.samples) if s["w"] == key), None)
                if last and last["t"] >= at: continue
                if last and last["p"] == pct and last["r"] == reset and at - last["t"] < 300: continue
                new.append({"t": at, "w": key, "p": pct, "r": reset})
            if not new: return
            self.samples += new
            cut = time.time() - self.keep
            if self.samples[0]["t"] < cut:
                self.samples = [s for s in self.samples if s["t"] >= cut]
                self._write(self.samples, "w")
            else:
                self._write(new, "a")

    def _write(self, rows, mode):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, mode) as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
        except OSError:
            pass

    def forecast(self, key, now=None):
        """{"eta": when it hits 100% at the current pace, or None, "rate": % per hour}."""
        now = now or time.time(); lookback = WINDOWS[key][1]
        with self.lock:
            s = [x for x in self.samples if x["w"] == key]
        if not s: return None
        cur = s[-1]
        win = [x for x in s if x["r"] == cur["r"] and x["t"] >= cur["t"] - lookback]
        if len(win) < 2 or cur["t"] - win[0]["t"] < 900: return {"eta": None, "rate": None}
        rate = (cur["p"] - win[0]["p"]) / (cur["t"] - win[0]["t"])   # % per second
        if rate <= 0: return {"eta": None, "rate": 0}
        eta = cur["t"] + (100 - cur["p"]) / rate
        return {"eta": eta if eta < cur["r"] else None, "rate": round(rate * 3600, 1)}


def summary(entries, status_dir, history, today=None, project=None):
    """entries: iterable of transcript Entry objects; project(cwd) names the project a folder belongs to."""
    today = today or datetime.date.today()
    week = {(today - datetime.timedelta(days=i)).isoformat(): 0 for i in range(6, -1, -1)}
    by_proj = {}; tot = {"today": [0, 0, 0, 0], "week": [0, 0, 0, 0]}
    for e in entries:
        proj = os.path.basename(((project or str)(e.cwd or "")).rstrip("/")) or "~"
        for day, v in e.days.items():
            if day in week:
                n = v[0] + v[1] + v[3]   # fresh input + output + cache writes (cache reads are cheap re-reads)
                week[day] += n; by_proj[proj] = by_proj.get(proj, 0) + n
                tot["week"] = [a + b for a, b in zip(tot["week"], v)]
                if day == today.isoformat(): tot["today"] = [a + b for a, b in zip(tot["today"], v)]
    limits = None; cost = 0.0; now = time.time()
    for f in sorted(glob.glob(os.path.join(status_dir, "*.json")), key=os.path.getmtime):
        try:
            with open(f) as fh: d = json.load(fh)
            at = os.path.getmtime(f)
            if at > now - 86400: cost += float((d.get("cost") or {}).get("total_cost_usd") or 0)
            if d.get("rate_limits"):
                limits = {"data": d["rate_limits"], "at": at}
                history.add(at, d["rate_limits"])
        except Exception:
            pass
    if limits:
        limits["forecast"] = {k: history.forecast(k) for k in WINDOWS}
    return {"week": [[k, v] for k, v in week.items()], "by_project": sorted(by_proj.items(), key=lambda x: -x[1])[:5],
            "today": tot["today"], "week_tot": tot["week"], "limits": limits, "cost_24h": round(cost, 2)}
