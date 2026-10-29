import json, os, shlex, sys, tempfile, unittest, unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ["CHATDECK_DIR"] = tempfile.mkdtemp()   # never touch the real ~/.chatdeck
from chatdeck import config, hooks

SPACED = "/Users/jo smith/.local/bin/chatdeck"


class QuotingTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "settings.json")
        config.save({})   # other tests may have left a wrapped status line behind

    def write(self, d):
        with open(self.path, "w") as f: json.dump(d, f)

    def read(self):
        with open(self.path) as f: return json.load(f)

    def old_install(self):
        old = SPACED + " hook"
        self.write({"statusLine": {"type": "command", "command": old + " statusline"},
                    "hooks": {"Notification": [{"matcher": hooks.WAITING_MATCHER,
                                                "hooks": [{"type": "command", "command": old + " waiting"}]}]}})

    def test_command_quotes_spaced_paths(self):
        with unittest.mock.patch("shutil.which", return_value=SPACED):
            self.assertEqual(shlex.split(hooks.command()), [SPACED])
        with unittest.mock.patch("shutil.which", return_value=None), \
             unittest.mock.patch.object(sys, "executable", "/opt/my py/python3"):
            parts = shlex.split(hooks.command())
        self.assertTrue(parts[0].startswith("PYTHONPATH="))
        self.assertEqual(parts[1:], ["/opt/my py/python3", "-m", "chatdeck"])

    def test_normal_path_unchanged(self):
        with unittest.mock.patch("shutil.which", return_value="/usr/local/bin/chatdeck"):
            self.assertEqual(hooks.command(), "/usr/local/bin/chatdeck")

    def test_install_writes_quoted_commands_and_uninstall_removes(self):
        with unittest.mock.patch("shutil.which", return_value=SPACED):
            hooks.install(self.path)
        s = self.read()
        self.assertEqual(shlex.split(s["statusLine"]["command"]), [SPACED, "hook", "statusline"])
        self.assertEqual(shlex.split(s["hooks"]["Notification"][0]["hooks"][0]["command"]), [SPACED, "hook", "waiting"])
        hooks.uninstall(self.path)
        self.assertEqual(self.read(), {})

    def test_reinstall_over_old_unquoted_install(self):
        self.old_install()
        with unittest.mock.patch("shutil.which", return_value=SPACED):
            hooks.install(self.path)
        s = self.read()
        self.assertEqual(shlex.split(s["statusLine"]["command"]), [SPACED, "hook", "statusline"])
        self.assertEqual(len(s["hooks"]["Notification"]), 1)
        self.assertEqual(len(s["hooks"]["Notification"][0]["hooks"]), 1)
        self.assertNotIn("statusline_command", config.load())   # old install wasn't mistaken for the user's own

    def test_uninstall_removes_old_unquoted_install(self):
        self.old_install()
        hooks.uninstall(self.path)
        self.assertEqual(self.read(), {})


if __name__ == "__main__":
    unittest.main()
