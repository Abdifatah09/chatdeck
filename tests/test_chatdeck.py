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

    def test_active_is_when_you_last_typed_not_when_the_file_changed(self):
        self.write(user("hello") + assistant([{"type": "text", "text": "hi"}], ts="2026-10-20T10:05:00Z"))
        e = self.idx.get(self.path); typed = e.active
        self.assertEqual(typed, 1792490400.0)
        self.write(line(type="system", subtype="away_summary", isMeta=True, timestamp="2026-10-20T10:08:00Z")
                   + line(type="artifact-autoreact-ledger", sessionId=SID)
                   + line(type="user", message={"content": [{"type": "tool_result", "content": "ok"}]}, timestamp="2026-10-20T10:09:00Z"))
        self.assertEqual(self.idx.get(self.path).active, typed)

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

    def test_chat_belongs_where_it_did_most_of_its_work(self):
        from chatdeck import server
        import subprocess
        repo = tempfile.mkdtemp(); os.makedirs(os.path.join(repo, "src"))
        subprocess.run(["git", "init", "-q", repo], check=True)
        e = transcripts.Entry(0)
        for cwd in ["/home/me"] * 3 + [repo, os.path.join(repo, "src")] * 2:   # 3 lines at home, 4 in the repo
            e.feed({"type": "user", "cwd": cwd, "message": {"content": "hi"}})
        self.assertEqual(server.chat_project(e, "/home/me"), server.project(repo))
        self.assertEqual(server.chat_project(None, "/home/me"), "/home/me")


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


class RenameRunningTest(Tmp):
    def test_name_shows_now_and_is_saved_when_the_chat_ends(self):
        from chatdeck import server
        self.write(user("fix the login bug"))
        idx, running = transcripts.Index(self.dir), {SID: {"name": "auto-name", "status": "idle"}}
        with unittest.mock.patch.object(server, "INDEX", idx), unittest.mock.patch.object(server, "PROJECTS", self.dir), \
             unittest.mock.patch.object(server, "agents", lambda fresh=False: running):
            with unittest.mock.patch.object(server.terminals, "type_into", lambda *a: False):   # terminal not reachable
                r = server.rename(SID, "Login bug")
            self.assertTrue(r["pending"])
            self.assertEqual(idx.get(self.path).title, "fix the login bug")          # transcript untouched while running
            self.assertEqual(next(c for c in server.state()["chats"] if c["sid"] == SID)["name"], "Login bug")
            running.clear()                                                           # the chat ends
            self.assertEqual(next(c for c in server.state()["chats"] if c["sid"] == SID)["name"], "Login bug")
            self.assertEqual(idx.get(self.path).title, "Login bug")
            self.assertEqual(server.pending_names(), {})


    def test_idle_chat_gets_rename_typed_and_rename_in_claude_wins(self):
        from chatdeck import server
        self.write(user("x"))
        idx, running, typed = transcripts.Index(self.dir), {SID: {"name": "auto", "status": "idle", "kind": "interactive", "pid": 1}}, []
        with unittest.mock.patch.object(server, "INDEX", idx), unittest.mock.patch.object(server, "PROJECTS", self.dir), \
             unittest.mock.patch.object(server, "agents", lambda fresh=False: running), \
             unittest.mock.patch.object(server.terminals, "type_into", lambda pid, name, text: typed.append(text) or True):
            self.assertTrue(server.rename(SID, "Login bug")["typed"])
            self.assertEqual(typed, ["/rename Login bug"]); self.assertEqual(server.pending_names(), {})
            running[SID]["status"] = "busy"
            self.assertTrue(server.rename(SID, "Later name")["busy"])        # busy: nothing typed, kept for later
            self.assertEqual(len(typed), 1)
            running[SID]["name"] = "renamed-in-claude"; server.state()       # /rename in Claude Code meanwhile
            self.assertEqual(server.pending_names(), {})


    def test_compact_only_types_into_idle_chats(self):
        from chatdeck import server
        self.write(user("x"))
        running, typed = {SID: {"name": "auto", "status": "busy", "kind": "interactive", "pid": 1}}, []
        with unittest.mock.patch.object(server, "INDEX", transcripts.Index(self.dir)), unittest.mock.patch.object(server, "PROJECTS", self.dir), \
             unittest.mock.patch.object(server, "agents", lambda fresh=False: running), \
             unittest.mock.patch.object(server.terminals, "type_into", lambda pid, name, text: typed.append(text) or True):
            self.assertFalse(server.compact(SID)["ok"]); self.assertEqual(typed, [])
            running[SID]["status"] = "idle"
            self.assertTrue(server.compact(SID)["ok"]); self.assertEqual(typed, ["/compact"])

    def test_auto_rename_types_bare_rename_only_when_idle(self):
        from chatdeck import server
        self.write(user("x"))
        running, typed = {SID: {"name": "auto", "status": "busy", "kind": "interactive", "pid": 1}}, []
        with unittest.mock.patch.object(server, "INDEX", transcripts.Index(self.dir)), unittest.mock.patch.object(server, "PROJECTS", self.dir), \
             unittest.mock.patch.object(server, "agents", lambda fresh=False: running), \
             unittest.mock.patch.object(server.terminals, "type_into", lambda pid, name, text: typed.append(text) or True):
            self.assertFalse(server.auto_rename(SID)["ok"]); self.assertEqual(typed, [])
            running[SID]["status"] = "idle"
            self.assertTrue(server.auto_rename(SID)["ok"]); self.assertEqual(typed, ["/rename"])
            self.assertIn("isn't running", server.auto_rename("00000000-0000-0000-0000-000000000000")["error"])


class NewChatTest(Tmp):
    def test_opens_only_known_project_folders(self):
        from chatdeck import server
        proj = tempfile.mkdtemp()
        self.write(line(type="user", message={"role": "user", "content": "hi"}, cwd=proj))
        launched = []
        with unittest.mock.patch.object(server, "INDEX", self.idx), unittest.mock.patch.object(server, "agents", lambda fresh=False: {}), \
             unittest.mock.patch.object(server.terminals, "launch", lambda cwd, cmd: launched.append((cwd, cmd)) or True):
            self.assertTrue(server.new_chat(server.project(proj))["ok"])
            self.assertEqual(launched, [(proj, "claude")])
            self.assertEqual(server.new_chat("/etc")["error"], "unknown project")


class ProjectsTest(Tmp):
    def test_added_folder_is_listed_and_can_start_chats(self):
        from chatdeck import server
        proj = tempfile.mkdtemp(); launched = []
        with unittest.mock.patch.object(server, "INDEX", self.idx), unittest.mock.patch.object(server, "agents", lambda fresh=False: {}), \
             unittest.mock.patch.object(server.terminals, "launch", lambda cwd, cmd: launched.append((cwd, cmd)) or True):
            self.assertIn("no folder", server.add_project("/no/such/dir")["error"])
            r = server.add_project(proj); key = r["project"]
            self.assertTrue(r["ok"]); self.assertIn(key, server.state()["projects"])
            self.assertTrue(server.add_project(proj)["already"])
            self.assertTrue(server.new_chat(key)["ok"]); self.assertEqual(launched, [(proj, "claude")])
            self.assertTrue(server.delete_project(key)["ok"])
            self.assertNotIn(key, server.state()["projects"]); self.assertTrue(os.path.isdir(proj))   # folder kept

    def test_delete_removes_its_chats_but_not_while_one_runs(self):
        from chatdeck import server
        proj = tempfile.mkdtemp(); running = {SID: {"cwd": proj}}
        self.write(line(type="user", message={"role": "user", "content": "hi"}, cwd=proj))
        with unittest.mock.patch.object(server, "INDEX", self.idx), unittest.mock.patch.object(server, "PROJECTS", self.dir), \
             unittest.mock.patch.object(server, "agents", lambda fresh=False: running):
            key = server.project(proj)
            self.assertIn("running", server.delete_project(key)["error"]); self.assertTrue(os.path.exists(self.path))
            running.clear()
            self.assertEqual(server.delete_project(key), {"ok": True, "deleted": 1})
            self.assertFalse(os.path.exists(self.path)); self.assertTrue(os.path.isdir(proj))
            self.assertEqual(server.delete_project(key)["error"], "unknown project")

    def test_deleting_every_chat_leaves_the_project_listed_empty(self):
        from chatdeck import server
        proj = tempfile.mkdtemp()
        self.write(line(type="user", message={"role": "user", "content": "hi"}, cwd=proj))
        with unittest.mock.patch.object(server, "INDEX", self.idx), unittest.mock.patch.object(server, "PROJECTS", self.dir), \
             unittest.mock.patch.object(server, "agents", lambda fresh=False: {}):
            key = server.project(proj)
            self.assertEqual(server.delete([SID])["deleted"], [SID])
            self.assertIn(key, server.state()["projects"])
            self.assertEqual(server.delete_project(key), {"ok": True, "deleted": 0})
            self.assertNotIn(key, server.state()["projects"])


class WslLaunchTest(unittest.TestCase):
    def test_new_window_by_default_tabs_when_asked(self):
        from chatdeck import terminals
        calls = []
        with unittest.mock.patch.object(terminals, "_win_exe", lambda *a: "wt.exe"), \
             unittest.mock.patch.object(terminals, "_spawn", lambda args, **kw: calls.append(args) or True):
            terminals._wsl_launch("/w/proj", "claude")
            with unittest.mock.patch.dict(os.environ, {"CHATDECK_TABS": "true"}):
                terminals._wsl_launch("/w/proj", "claude")
        self.assertEqual(calls[0][1:3], ["-w", "new"]); self.assertEqual(calls[1][1:4], ["-w", "0", "new-tab"])


class CompactTest(Tmp):
    def test_context_shrinks_at_the_compact_boundary(self):
        self.write(user("hi") + assistant([{"type": "text", "text": "hello"}], usage_={"input_tokens": 385000, "output_tokens": 5}))
        self.assertEqual(self.idx.get(self.path).ctx, 385000)
        self.write(line(type="system", subtype="compact_boundary", compactMetadata={"preTokens": 385000, "postTokens": 17785}))
        self.assertEqual(self.idx.get(self.path).ctx, 17785)


class DemoTest(unittest.TestCase):
    def test_demo_home_has_running_saved_waiting_and_limits(self):
        from chatdeck import demo
        root = tempfile.mkdtemp(); data = demo.build(root)
        idx = transcripts.Index(os.path.join(root, ".claude", "projects"))
        chats = idx.all()
        with open(os.path.join(data, "demo-agents.json")) as f: running = json.load(f)
        self.assertEqual(len(chats), len(demo.CHATS) + 1)   # plus one empty chat
        self.assertEqual({a["sessionId"] for a in running} - set(chats), set())
        self.assertEqual(len(os.listdir(os.path.join(data, "waiting"))), 1)
        hist = usage.LimitHistory(os.path.join(data, "limits-history.jsonl"))
        s = usage.summary([e for _, e in chats.values()], os.path.join(data, "status"), hist)
        self.assertEqual(s["limits"]["data"]["five_hour"]["used_percentage"], 62)
        self.assertIsNotNone(s["limits"]["forecast"]["five_hour"]["eta"])


if __name__ == "__main__":
    unittest.main()
