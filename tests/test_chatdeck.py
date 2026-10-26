import json, os, sys, tempfile, time, unittest, unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ["CHATDECK_DIR"] = tempfile.mkdtemp()   # never touch the real ~/.chatdeck
from chatdeck import config, hooks, transcripts, usage

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
        from chatdeck import server
        d = tempfile.mkdtemp(); server.WAITING = d
        with open(os.path.join(d, SID + ".json"), "w") as f: json.dump({"at": 1000, "message": "needs Bash"}, f)
        class E: mtime = 999
        act = {SID: {}}
        self.assertEqual(server.waiting(act, {SID: ("p", E)}), {SID: "needs Bash"})
        E.mtime = 1010
        self.assertEqual(server.waiting(act, {SID: ("p", E)}), {})
        self.assertEqual(server.waiting({}, {SID: ("p", E)}), {})   # not running


class TerminalTest(unittest.TestCase):
    def test_shell_line_quotes_folder(self):
        from chatdeck import terminals
        line = terminals._shell_line("/tmp/it's here", "claude --resume x")
        self.assertTrue(line.startswith("cd '/tmp/it'\"'\"'s here' && claude --resume x; exec "))

    def test_tty_of_unknown_pid(self):
        from chatdeck import terminals
        self.assertIsNone(terminals.tty_of(999999999))


class ProjectTest(unittest.TestCase):
    def test_worktrees_and_subfolders_belong_to_the_repo(self):
        import subprocess
        from chatdeck import server
        repo = tempfile.mkdtemp(); os.makedirs(os.path.join(repo, "src"))
        subprocess.run(["git", "init", "-q", repo], check=True)
        self.assertEqual(server.project(os.path.join(repo, "src")), server.project(repo))
        self.assertEqual(server.project(repo + "/.claude/worktrees/gone"), server.project(repo))
        self.assertEqual(server.project("/no/such/dir"), "/no/such/dir")


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "settings.json")
        if os.path.exists(config.FILE): os.remove(config.FILE)

    def write(self, d):
        with open(self.path, "w") as f: json.dump(d, f)

    def read(self):
        with open(self.path) as f: return json.load(f)

    def test_keeps_existing_status_line_and_hooks_then_restores_them(self):
        mine = {"type": "command", "command": "~/my-status.sh", "padding": 1}
        other = {"matcher": "permission_prompt|elicitation_dialog", "hooks": [{"type": "command", "command": "notify.sh"}]}
        self.write({"statusLine": mine, "hooks": {"Notification": [other], "Stop": [{"hooks": [{"type": "command", "command": "x"}]}]},
                    "model": "opus"})
        hooks.install(self.path, cmd="/bin/chatdeck"); hooks.install(self.path, cmd="/bin/chatdeck")   # twice: no duplicates
        s = self.read()
        self.assertEqual(s["statusLine"], {"type": "command", "command": "/bin/chatdeck hook statusline", "padding": 1})
        self.assertEqual(config.load()["statusline_command"], "~/my-status.sh")
        cmds = [h["command"] for h in s["hooks"]["Notification"][0]["hooks"]]
        self.assertEqual(cmds, ["notify.sh", "/bin/chatdeck hook waiting"])
        hooks.uninstall(self.path)
        s = self.read()
        self.assertEqual(s["statusLine"], mine)
        self.assertEqual(s["hooks"]["Notification"], [other])
        self.assertEqual(s["model"], "opus"); self.assertIn("Stop", s["hooks"])

    def test_fresh_settings_and_legacy_hook_cleanup(self):
        self.write({"hooks": {"Notification": [{"matcher": "x", "hooks": [{"type": "command", "command": "python3 ~/chatdeck/hooks/waiting.py"}]}]}})
        hooks.install(self.path, cmd="cd")
        groups = self.read()["hooks"]["Notification"]
        self.assertEqual([g["matcher"] for g in groups], ["permission_prompt|elicitation_dialog"])
        hooks.uninstall(self.path)
        self.assertEqual(self.read(), {})

    def test_statusline_hook_saves_payload_and_runs_wrapped(self):
        import io
        config.save({"statusline_command": "cat >/dev/null; echo hello"})
        out = io.StringIO()
        hooks.statusline(io.StringIO(json.dumps({"session_id": SID})), out)
        self.assertEqual(out.getvalue(), "hello\n")
        self.assertTrue(os.path.exists(os.path.join(config.DATA, "status", SID + ".json")))
        hooks.waiting(io.StringIO(json.dumps({"session_id": SID, "message": "needs Bash"})))
        with open(os.path.join(config.DATA, "waiting", SID + ".json")) as f: self.assertEqual(json.load(f)["message"], "needs Bash")


class CliTest(unittest.TestCase):
    def test_plain_options_mean_open(self):
        from chatdeck import cli
        with unittest.mock.patch.object(cli, "cmd_open") as m:
            cli.main(["--no-open", "--port", "1"]); a = m.call_args[0][0]
        self.assertEqual((a.no_open, a.port), (True, 1))


if __name__ == "__main__":
    unittest.main()
