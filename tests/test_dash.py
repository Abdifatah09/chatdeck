import json, os, sys, tempfile, time, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transcripts, usage

SID = "11111111-2222-3333-4444-555555555555"


def line(**d): return json.dumps(d) + "\n"
def user(text, **kw): return line(type="user", message={"role": "user", "content": text}, timestamp="2026-10-20T10:00:00Z", cwd="/w/proj", **kw)
def assistant(content, mid="m1", usage_=None, ts="2026-10-20T10:00:01Z"):
    return line(type="assistant", timestamp=ts, message={"id": mid, "role": "assistant", "content": content,
                                                         "usage": usage_ or {"input_tokens": 10, "output_tokens": 5}})


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); os.makedirs(os.path.join(self.dir, "-w-proj"))
        self.path = os.path.join(self.dir, "-w-proj", SID + ".jsonl")
        self.idx = transcripts.Index(self.dir)

    def write(self, s, mode="a"):
        with open(self.path, mode) as f: f.write(s)


class IndexTest(Tmp):
    def test_reads_only_appended_lines(self):
        self.write(user("fix the jt808 clock") + assistant([{"type": "text", "text": "Done, the clock is fixed."}]))
        e = self.idx.get(self.path)
        self.assertEqual((e.msgs, e.first, e.cwd), (1, "fix the jt808 clock", "/w/proj"))
        off = e.offset
        self.write(user("thanks"))
        e2 = self.idx.get(self.path)
        self.assertIs(e, e2); self.assertGreater(e.offset, off); self.assertEqual(e.msgs, 2)

    def test_half_written_line_waits(self):
        whole = user("hello")
        self.write(whole[:15])
        self.assertEqual(self.idx.get(self.path).msgs, 0)
        self.write(whole[15:])
        self.assertEqual(self.idx.get(self.path).msgs, 1)

    def test_rewritten_file_is_reread(self):
        self.write(user("one") + user("two"))
        self.assertEqual(self.idx.get(self.path).msgs, 2)
        self.write(user("x"), mode="w")
        self.assertEqual(self.idx.get(self.path).msgs, 1)

    def test_title_priority_and_cost(self):
        self.write(user("first message") + line(type="ai-title", aiTitle="AI name"))
        self.assertEqual(self.idx.get(self.path).title, "AI name")
        self.write(line(type="custom-title", customTitle="Mine") + line(type="cost-state", totalCostUSD=1.234))
        e = self.idx.get(self.path)
        self.assertEqual((e.title, round(e.cost, 2)), ("Mine", 1.23))

    def test_ignores_injected_context_and_counts_usage_once(self):
        u = {"input_tokens": 100, "output_tokens": 7, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 3}
        self.write(user("<command-name>/clear</command-name>") + user("ctx", isMeta=True)
                   + line(type="user", message={"content": [{"type": "text", "text": "<system-reminder>x</system-reminder>"}]})
                   + assistant([{"type": "thinking", "thinking": ""}], usage_=u) + assistant([{"type": "text", "text": "hi"}], usage_=u))
        e = self.idx.get(self.path)
        self.assertEqual(e.msgs, 0)
        self.assertEqual(e.days["2026-10-20"], [100, 7, 50, 3])
        self.assertEqual(e.ctx, 153)

    def test_search_needs_every_word(self):
        self.write(user("deploy is slow") + assistant([{"type": "text", "text": "The Docker build cache was cold."}]))
        self.assertIn(SID, self.idx.search("docker cold"))
        self.assertNotIn(SID, self.idx.search("docker warm"))
        self.assertIn("Docker build", self.idx.search("docker")[SID])

    def test_deleted_files_are_forgotten(self):
        self.write(user("x")); self.idx.all(); os.remove(self.path)
        self.assertEqual(self.idx.all(), {}); self.assertEqual(self.idx.entries, {})


class ViewerTest(Tmp):
    def test_messages_and_markdown(self):
        self.write(user("run the tests")
                   + assistant([{"type": "text", "text": "Running them."},
                                {"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}}])
                   + line(type="user", message={"content": [{"type": "tool_result", "content": "ok\n" * 2000, "is_error": False}]})
                   + line(type="user", message={"content": "side"}, isSidechain=True)
                   + line(type="system", subtype="compact_boundary"))
        items = transcripts.messages(self.path)["items"]
        self.assertEqual([i["k"] for i in items], ["user", "assistant", "tool", "result", "compact"])
        self.assertEqual(items[2]["text"], "npm test")
        self.assertTrue(items[3]["cut"]); self.assertEqual(len(items[3]["text"]), 1500)
        md = transcripts.markdown(self.path, "My chat")
        self.assertIn("# My chat", md); self.assertIn("## You\n\nrun the tests", md); self.assertIn("`npm test`", md)

    def test_cap_keeps_latest(self):
        self.write("".join(user(f"m{i}") for i in range(10)))
        r = transcripts.messages(self.path, max_items=3)
        self.assertEqual(([i["text"] for i in r["items"]], r["hidden"]), (["m7", "m8", "m9"], 7))

    def test_rename_appends_on_a_new_line(self):
        self.write(user("x").rstrip("\n"))      # file without a trailing newline
        transcripts.rename(self.path, SID, "New name")
        self.assertEqual(self.idx.get(self.path).title, "New name")
        self.assertEqual(self.idx.get(self.path).msgs, 1)


T0 = time.time() - 86400   # samples older than the 8-day history are dropped


class ForecastTest(unittest.TestCase):
    def hist(self, pts, reset=10**6):
        h = usage.LimitHistory(os.path.join(tempfile.mkdtemp(), "h.jsonl"))
        for t, p in pts: h.add(T0 + t, {"five_hour": {"used_percentage": p, "resets_at": T0 + reset}})
        return h

    def test_eta_from_pace(self):
        f = self.hist([(0, 10), (1800, 20)]).forecast("five_hour")   # 10% per 30 min
        self.assertEqual(f["rate"], 20.0); self.assertAlmostEqual(f["eta"], T0 + 1800 + 4 * 3600)

    def test_no_eta_when_reset_comes_first(self):
        f = self.hist([(0, 10), (1800, 20)], reset=5000).forecast("five_hour")
        self.assertIsNone(f["eta"]); self.assertGreater(f["rate"], 0)

    def test_needs_enough_history_and_ignores_old_window(self):
        self.assertIsNone(self.hist([(0, 10), (300, 20)]).forecast("five_hour")["rate"])
        h = self.hist([(0, 90)], reset=100)
        h.add(T0 + 1000, {"five_hour": {"used_percentage": 1, "resets_at": T0 + 20000}})
        h.add(T0 + 2000, {"five_hour": {"used_percentage": 1, "resets_at": T0 + 20000}})
        self.assertEqual(h.forecast("five_hour")["rate"], 0)

    def test_history_survives_restart(self):
        h = self.hist([(0, 10), (1800, 20)])
        self.assertEqual(len(usage.LimitHistory(h.path).samples), 2)


class WaitingTest(unittest.TestCase):
    def test_cleared_once_transcript_moves_on(self):
        import server
        d = tempfile.mkdtemp(); server.WAITING = d
        with open(os.path.join(d, SID + ".json"), "w") as f: json.dump({"at": 1000, "message": "needs Bash"}, f)
        class E: mtime = 999
        act = {SID: {}}
        self.assertEqual(server.waiting(act, {SID: ("p", E)}), {SID: "needs Bash"})
        E.mtime = 1010
        self.assertEqual(server.waiting(act, {SID: ("p", E)}), {})
        self.assertEqual(server.waiting({}, {SID: ("p", E)}), {})   # not running


if __name__ == "__main__":
    unittest.main()
