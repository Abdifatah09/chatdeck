"""Reading Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl).

The index reads each file incrementally: it remembers how far it got and only parses
lines appended since, so a 30 MB chat that grows by one message costs one message."""
import json, os, re, threading

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TAG = re.compile(r"<(system-reminder|local-command-[a-z]+|command-[a-z]+)>.*?</\1>", re.S)


def _user_text(c):
    """Text the person typed, or None for tool results, command output and injected context."""
    if isinstance(c, str):
        return None if c.lstrip().startswith("<") else c.strip() or None
    if isinstance(c, list):
        parts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        t = TAG.sub("", "\n".join(parts)).strip()
        return t or None
    return None


def _assistant_text(c):
    if not isinstance(c, list): return ""
    return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text").strip()


class Entry:
    __slots__ = ("ino", "offset", "size", "mtime", "custom", "ai", "summary", "first", "cwd", "ctx", "msgs",
                 "days", "seen", "cost", "text", "text_lc")

    def __init__(self, ino):
        self.ino = ino; self.offset = 0; self.size = 0; self.mtime = 0.0
        self.custom = self.ai = self.summary = self.first = self.cwd = None
        self.ctx = self.msgs = 0; self.days = {}; self.seen = set(); self.cost = 0.0
        self.text = []; self.text_lc = ""

    @property
    def title(self):
        return self.custom or self.ai or self.summary or self.first

    def feed(self, d):
        t = d.get("type")
        if t == "custom-title" and d.get("customTitle"): self.custom = d["customTitle"]
        elif t == "ai-title" and d.get("aiTitle"): self.ai = d["aiTitle"]
        elif t == "summary" and d.get("summary"): self.summary = d["summary"]
        elif t == "cost-state": self.cost = float(d.get("totalCostUSD") or 0)
        elif t == "system" and d.get("subtype") == "compact_boundary":   # context shrinks right away, not at the next reply
            self.ctx = (d.get("compactMetadata") or {}).get("postTokens") or self.ctx
        if d.get("cwd"): self.cwd = d["cwd"]
        if d.get("isSidechain") or d.get("isMeta"): return
        m = d.get("message") or {}
        if t == "user":
            txt = _user_text(m.get("content"))
            if txt:
                self.msgs += 1; self.first = self.first or txt.replace("\n", " ")[:90]
                self._add_text(txt)
        elif t == "assistant":
            u = m.get("usage") or {}
            v = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            if v: self.ctx = v
            mid = m.get("id"); ts = d.get("timestamp", "")
            if u and mid and mid not in self.seen and len(ts) >= 10:
                self.seen.add(mid); day = self.days.setdefault(ts[:10], [0, 0, 0, 0])
                day[0] += u.get("input_tokens", 0); day[1] += u.get("output_tokens", 0)
                day[2] += u.get("cache_read_input_tokens", 0); day[3] += u.get("cache_creation_input_tokens", 0)
            txt = _assistant_text(m.get("content"))
            if txt: self._add_text(txt)

    def _add_text(self, s):
        self.text.append(s); self.text_lc += s.lower() + "\n"

    def info(self):
        return {"title": self.title, "cwd": self.cwd, "ctx": self.ctx, "msgs": self.msgs, "days": self.days,
                "cost": round(self.cost, 2), "size": self.size, "mtime": self.mtime}


class Index:
    def __init__(self, root):
        self.root = root; self.entries = {}; self.lock = threading.Lock()

    def files(self):
        out = {}
        try:
            for proj in os.scandir(self.root):
                if not proj.is_dir(): continue
                for f in os.scandir(proj.path):
                    if f.name.endswith(".jsonl") and UUID.match(f.name[:-6]): out[f.name[:-6]] = f.path
        except FileNotFoundError:
            pass
        return out

    def get(self, path):
        """The entry for a transcript, reading only what was appended since last time."""
        with self.lock:
            st = os.stat(path); e = self.entries.get(path)
            if e is None or e.ino != st.st_ino or st.st_size < e.offset:   # new, replaced or truncated
                e = self.entries[path] = Entry(st.st_ino)
            if st.st_size > e.offset:
                with open(path, "rb") as f:
                    f.seek(e.offset); chunk = f.read(st.st_size - e.offset)
                end = chunk.rfind(b"\n") + 1      # leave a half-written last line for next time
                for line in chunk[:end].splitlines():
                    try: e.feed(json.loads(line))
                    except Exception: pass
                e.offset += end
            e.size = st.st_size; e.mtime = st.st_mtime
            return e

    def all(self):
        """{sid: (path, Entry)} for every transcript; forgets deleted ones."""
        fs = self.files(); out = {}
        for sid, p in fs.items():
            try: out[sid] = (p, self.get(p))
            except FileNotFoundError: pass
        with self.lock:
            for p in [p for p in self.entries if p not in set(fs.values())]: del self.entries[p]
        return out

    def forget(self, path):
        with self.lock: self.entries.pop(path, None)

    def search(self, q, limit=200):
        """Chats whose messages contain every word of q, with a snippet around the first hit."""
        words = q.lower().split()
        if not words: return {}
        hits = {}
        for sid, (_, e) in self.all().items():
            if all(w in e.text_lc for w in words):
                hits[sid] = snippet(e, words[0])
                if len(hits) >= limit: break
        return hits


def snippet(e, word, pad=60):
    for s in e.text:
        i = s.lower().find(word)
        if i >= 0:
            a, b = max(0, i - pad), min(len(s), i + len(word) + pad)
            return ("…" if a else "") + s[a:b].replace("\n", " ") + ("…" if b < len(s) else "")
    return ""


def _tool_summary(name, inp):
    if not isinstance(inp, dict): return ""
    for k in ("command", "file_path", "pattern", "url", "query", "description", "prompt", "skill"):
        if inp.get(k): return str(inp[k])[:300]
    return json.dumps(inp)[:300]


def _result_text(c):
    if isinstance(c, str): return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type')}]" for b in c if isinstance(b, dict))
    return ""


def messages(path, max_items=1500, result_chars=1500):
    """The conversation as a flat list of items for the viewer:
    {"k": "user"|"assistant"|"tool"|"result"|"command"|"compact", "text": ..., "ts": ..., ...}"""
    items = []
    with open(path, "rb") as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            if d.get("isSidechain") or d.get("isMeta"): continue
            t = d.get("type"); ts = d.get("timestamp"); m = d.get("message") or {}; c = m.get("content")
            if t == "system" and d.get("subtype") == "compact_boundary":
                items.append({"k": "compact", "ts": ts})
            elif t == "user":
                if isinstance(c, str) and "<command-name>" in c:
                    cmd = re.search(r"<command-name>(.*?)</command-name>", c, re.S)
                    args = re.search(r"<command-args>(.*?)</command-args>", c, re.S)
                    if cmd: items.append({"k": "command", "text": (cmd.group(1) + " " + (args.group(1) if args else "")).strip(), "ts": ts})
                    continue
                txt = _user_text(c)
                if txt: items.append({"k": "user", "text": txt, "ts": ts})
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            r = _result_text(b.get("content"))
                            items.append({"k": "result", "text": r[:result_chars], "cut": len(r) > result_chars,
                                          "err": bool(b.get("is_error")), "ts": ts})
            elif t == "assistant" and isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict): continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        items.append({"k": "assistant", "text": b["text"].strip(), "ts": ts})
                    elif b.get("type") == "tool_use":
                        items.append({"k": "tool", "name": b.get("name", ""), "text": _tool_summary(b.get("name"), b.get("input")), "ts": ts})
    return {"items": items[-max_items:], "hidden": max(0, len(items) - max_items)}


def markdown(path, title):
    out = [f"# {title}", ""]
    for it in messages(path, max_items=10**9)["items"]:
        k = it["k"]
        if k == "user": out += ["## You", "", it["text"], ""]
        elif k == "assistant": out += ["## Claude", "", it["text"], ""]
        elif k == "tool": out += [f"> **{it['name']}** `{it['text'].replace('`', "'")[:200]}`", ""]
        elif k == "command": out += [f"> `{it['text']}`", ""]
        elif k == "compact": out += ["---", "*conversation compacted*", ""]
    return "\n".join(out)


def rename(path, sid, title):
    """Give a saved chat a new name, the same way Claude Code's /rename records it."""
    with open(path, "rb") as f:
        f.seek(0, 2); ends_clean = True
        if f.tell(): f.seek(-1, 2); ends_clean = f.read(1) == b"\n"
    with open(path, "a") as f:
        if not ends_clean: f.write("\n")
        f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}) + "\n")
        f.write(json.dumps({"type": "agent-name", "agentName": title, "sessionId": sid}) + "\n")
