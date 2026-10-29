import json, os, subprocess, sys, tempfile, unittest, unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ["CHATDECK_DIR"] = tempfile.mkdtemp()   # never touch the real ~/.chatdeck
from chatdeck import config, server

SID = "11111111-2222-3333-4444-555555555555"
RUNNING = json.dumps([{"sessionId": SID, "pid": 1}])


def done(code, out=""): return subprocess.CompletedProcess([], code, out, "")


class DeleteSafetyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); os.makedirs(os.path.join(self.dir, "-w-proj"))
        self.path = os.path.join(self.dir, "-w-proj", SID + ".jsonl")
        with open(self.path, "w") as f: f.write("{}\n")
        for k, v in [("PROJECTS", self.dir), ("INDEX", server.transcripts.Index(self.dir))]:
            p = unittest.mock.patch.object(server, k, v); p.start(); self.addCleanup(p.stop)
        p = unittest.mock.patch.object(config, "DEMO", False); p.start(); self.addCleanup(p.stop)
        server._agents = (0.0, {})

    def run_delete(self, **kw):
        with unittest.mock.patch.object(server.subprocess, "run", **kw): return server.delete([SID])

    def test_nonzero_exit_refuses(self):
        r = self.run_delete(return_value=done(1))
        self.assertEqual((r["deleted"], r["refused"]), ([], [SID])); self.assertIn("couldn't check", r["error"])
        self.assertTrue(os.path.exists(self.path))

    def test_empty_stdout_refuses(self):
        r = self.run_delete(return_value=done(0, ""))
        self.assertEqual(r["refused"], [SID]); self.assertTrue(os.path.exists(self.path))

    def test_timeout_refuses(self):
        r = self.run_delete(side_effect=subprocess.TimeoutExpired("claude", 30))
        self.assertEqual(r["refused"], [SID]); self.assertTrue(os.path.exists(self.path))

    def test_failure_keeps_last_good_cache(self):
        self.run_delete(return_value=done(0, RUNNING)); self.run_delete(return_value=done(1))
        self.assertIn(SID, server.agents())

    def test_running_chat_refused(self):
        r = self.run_delete(return_value=done(0, RUNNING))
        self.assertEqual(r["refused"], [SID]); self.assertNotIn("error", r); self.assertTrue(os.path.exists(self.path))

    def test_deleted_when_check_succeeds_and_not_running(self):
        r = self.run_delete(return_value=done(0, "[]"))
        self.assertEqual((r["deleted"], r["refused"]), ([SID], [])); self.assertFalse(os.path.exists(self.path))

    def test_delete_project_and_stop_refuse_on_failed_check(self):
        with unittest.mock.patch.object(server.subprocess, "run", return_value=done(1)):
            self.assertFalse(server.delete_project("/w/proj")["ok"])
            self.assertFalse(server.stop(SID)["ok"])
        self.assertTrue(os.path.exists(self.path))


if __name__ == "__main__": unittest.main()
