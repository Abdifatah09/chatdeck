import json, os, sys, tempfile, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
os.environ["CHATDECK_DIR"] = tempfile.mkdtemp()   # never touch the real ~/.chatdeck
from chatdeck import usage


T = time.time() - 3600


def sample(t, p, r=10000, w="five_hour"): return {"t": t, "w": w, "p": p, "r": r}


class HistoryTest(unittest.TestCase):
    def path(self, *lines):
        p = os.path.join(tempfile.mkdtemp(), "h.jsonl")
        with open(p, "w") as f: f.write("\n".join(lines) + "\n")
        return p

    def test_corrupt_and_malformed_lines_skipped(self):
        good = [sample(T + 1, 5), sample(T + 2, 6)]
        h = usage.LimitHistory(self.path(json.dumps(good[0]), '{"t": 3, "w": "five', '{"w": "five_hour"}', "[1]", json.dumps(good[1])))
        self.assertEqual(h.samples, good)
        h.add(T + 5, {"five_hour": {"used_percentage": 7, "resets_at": 10000}})   # no KeyError
        self.assertEqual(len(h.samples), 3)

    def test_stale_window_has_no_eta(self):
        h = usage.LimitHistory(self.path()); h.samples = [sample(0, 10), sample(3600, 20)]
        self.assertEqual(h.forecast("five_hour", now=5000)["rate"], 10.0)
        self.assertEqual(h.forecast("five_hour", now=10000), {"eta": None, "rate": None})
        self.assertIsNone(h.forecast("five_hour", now=9000)["eta"])   # eta (36000) is past the reset
        h.samples = [sample(0, 10, 99999), sample(3600, 20, 99999)]
        self.assertEqual(h.forecast("five_hour", now=3700)["eta"], 3600 + 80 * 360)
        self.assertIsNone(h.forecast("five_hour", now=3600 + 80 * 360 + 1)["eta"])

    def test_cost_counts_only_recent_status_files(self):
        d = tempfile.mkdtemp()
        for name, cost, age in (("a", 1.5, 100), ("b", 2.0, 3 * 86400)):
            p = os.path.join(d, name + ".json")
            with open(p, "w") as f: json.dump({"cost": {"total_cost_usd": cost}}, f)
            os.utime(p, (time.time() - age,) * 2)
        h = usage.LimitHistory(os.path.join(d, "h.jsonl"))
        self.assertEqual(usage.summary([], d, h)["cost_24h"], 1.5)


if __name__ == "__main__": unittest.main()
