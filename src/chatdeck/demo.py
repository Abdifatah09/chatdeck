"""Demo mode: a made-up home folder with Claude Code chats in a few projects, some of them
"running", so chatdeck can be tried (and screenshotted) without real chats.

`chatdeck demo` builds it in a temp folder and runs the server with HOME pointing there."""
import datetime, json, os, random, time, uuid

# (project folder, title, days ago, context tokens, cost, running status, turns)
# A turn is (you, tool name, tool input, tool output, Claude's reply); tool parts may be None.
CHATS = [
    ("code/webshop", "Add Stripe checkout to the cart page", 0, 142_000, 3.84, "busy", [
        ("Add Stripe checkout to the cart page. Use their hosted checkout, not Elements.",
         "Read", {"file_path": "src/pages/cart.tsx"}, "export default function Cart() { ... }",
         "The cart page renders line items from `useCart()`. I'll add a server route that creates a Checkout "
         "Session and a **Pay** button that redirects to it."),
        ("Sounds good. Keep the test keys in .env.local",
         "Edit", {"file_path": "src/server/checkout.ts"}, "File updated",
         "Added `POST /api/checkout`. It reads `STRIPE_SECRET_KEY` from `.env.local` and returns the session URL."),
        ("Now handle the success and cancel pages",
         "Bash", {"command": "npm test -- checkout"}, "PASS  tests/checkout.test.ts (4 tests)",
         "Both pages are in, and the checkout tests pass. Running the webhook tests next."),
    ]),
    ("code/webshop/.claude/worktrees/dark-mode", "Dark mode for the storefront", 1, 64_000, 1.12, None, [
        ("Add a dark mode that follows the system setting",
         "Grep", {"pattern": "--color-bg"}, "src/styles/tokens.css:3",
         "Colors are already tokens in `tokens.css`, so dark mode is one `prefers-color-scheme` block."),
        ("Do it, and add a toggle in the footer", None, None, None,
         "Done: the toggle stores the choice in localStorage and falls back to the system setting."),
    ]),
    ("code/webshop", "Fix flaky cart total test", 2, 38_000, 0.61, None, [
        ("cart total test fails about 1 in 10 runs", "Bash", {"command": "npm test -- cart --repeat 20"},
         "2 failed, 18 passed", "It rounds floats. Prices should be integer cents - switching the cart to cents."),
    ]),
    ("code/api-server", "Rate-limit the public API", 0, 96_000, 2.07, "waiting", [
        ("Add rate limiting to the public API, 100 requests a minute per key",
         "Read", {"file_path": "app/middleware.py"}, "def auth_middleware(request): ...",
         "I'll add a token-bucket limiter after auth, backed by Redis so it works across workers."),
        ("Yes, and return Retry-After", "Bash", {"command": "pip install redis"}, None,
         "I need to install the `redis` package to continue."),
    ]),
    ("code/api-server", "Migrate users table to Postgres 16", 1, 210_000, 5.26, None, [
        ("We're moving from Postgres 12 to 16. Plan the users table migration",
         "Bash", {"command": "pg_dump --schema-only -t users"}, "CREATE TABLE users (...)",
         "The table uses `md5` passwords and an old `citext` version. Plan: upgrade extensions, switch to "
         "scram-sha-256, then run `pg_upgrade --link`."),
        ("Write it up as a runbook", None, None, None, "Wrote `docs/runbooks/postgres-16.md` with rollback steps."),
    ]),
    ("code/api-server", "Why is /orders slow?", 4, 51_000, 0.94, None, [
        ("GET /orders takes 3 seconds for big accounts", "Bash", {"command": "EXPLAIN ANALYZE SELECT ..."},
         "Seq Scan on order_items  (cost=0.00..48211.00)", "It scans `order_items`. An index on `order_id` fixes it: 3s to 40ms."),
    ]),
    ("code/mobile-app", "Push notifications on Android", 0, 188_000, 4.41, "idle", [
        ("Set up push notifications on Android with FCM", "Read", {"file_path": "android/app/build.gradle"}, "...",
         "Added the Firebase plugin and a `NotificationService`. You'll need to drop `google-services.json` into `android/app`."),
        ("Done, it's there. Test it", "Bash", {"command": "npx react-native run-android"}, "BUILD SUCCESSFUL",
         "A test notification arrives on the emulator, both in the foreground and the background."),
    ]),
    ("code/mobile-app", "Upgrade to React Native 0.76", 5, 120_000, 2.88, None, [
        ("Upgrade us to React Native 0.76", "Bash", {"command": "npx react-native upgrade 0.76"}, "Upgrade complete",
         "Upgraded. Two libraries needed new versions; the app builds on iOS and Android."),
    ]),
    ("code/blog", "Write a post about SQLite in production", 3, 44_000, 0.73, None, [
        ("Draft a blog post: why we run SQLite in production", None, None, None,
         "Here's a draft with three parts: why it's enough, how we back it up (Litestream), and where it isn't enough."),
    ]),
    ("dotfiles", "Tidy up my zsh config", 6, 22_000, 0.31, None, [
        ("My zsh takes 2 seconds to start", "Bash", {"command": "zsh -xv -i -c exit 2>&1 | tail"}, "nvm.sh ...",
         "Most of it is `nvm`. Loading it lazily brings startup to 0.2 seconds."),
    ]),
    ("", "Plan my week", 0, 9_000, 0.08, None, [
        ("Help me plan this week: ship checkout, the Postgres move, and the blog post", None, None, None,
         "Checkout Monday to Tuesday, the Postgres runbook review Wednesday, the migration Thursday night, "
         "and the blog post Friday."),
    ]),
]


def _line(f, d): f.write(json.dumps(d) + "\n")


def build(root):
    """Write the demo home into root (an empty folder). Returns the CHATDECK_DIR to use."""
    rnd = random.Random(7); now = time.time()
    projects = os.path.join(root, ".claude", "projects"); data = os.path.join(root, ".chatdeck")
    agents = []
    for i, (folder, title, ago, ctx, cost, run, turns) in enumerate(CHATS):
        run_name = {"Add Stripe checkout to the cart page": "stripe-checkout", "Rate-limit the public API": "api-rate-limit",
                    "Push notifications on Android": "android-push"}.get(title)
        cwd = os.path.join(root, folder) if folder else root
        os.makedirs(cwd, exist_ok=True)
        sid = str(uuid.UUID(int=rnd.getrandbits(128)))
        pdir = os.path.join(projects, cwd.replace("/", "-").replace(".", "-")); os.makedirs(pdir, exist_ok=True)
        path = os.path.join(pdir, sid + ".jsonl")
        end = now - ago * 86400 - rnd.randint(5, 50) * 60 if not run else now - rnd.randint(1, 4) * 60
        t = end - len(turns) * 600

        def base(kind):
            return {"type": kind, "timestamp": datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
                    .replace("+00:00", "Z"), "cwd": cwd, "sessionId": sid, "uuid": str(uuid.uuid4())}
        with open(path, "w") as f:
            for n, (you, tool, inp, out, reply) in enumerate(turns):
                _line(f, dict(base("user"), message={"role": "user", "content": you}))
                last = n == len(turns) - 1
                usage = {"input_tokens": rnd.randint(200, 2000), "output_tokens": rnd.randint(2000, 9000) * (1 + 6 * (ago > 0)),   # older chats stand for a whole day's work
                         "cache_creation_input_tokens": rnd.randint(min(8000, ctx // 4), min(30000, ctx // 2))}
                usage["cache_read_input_tokens"] = max(0, (ctx if last else ctx * (n + 1) // len(turns))
                                                       - usage["input_tokens"] - usage["cache_creation_input_tokens"])
                content = [{"type": "text", "text": reply}]
                if tool: content.append({"type": "tool_use", "id": f"tu{i}{n}", "name": tool, "input": inp})
                _line(f, dict(base("assistant"), message={"id": f"msg_{i}_{n}", "role": "assistant", "content": content, "usage": usage}))
                if tool and out:
                    t += 60
                    _line(f, dict(base("user"), message={"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": f"tu{i}{n}", "content": out}]}))
                t += 540
            _line(f, {"type": "ai-title", "aiTitle": title, "sessionId": sid})
            _line(f, {"type": "cost-state", "totalCostUSD": cost, "sessionId": sid})
        os.utime(path, (end, end))
        if run:
            agents.append({"pid": 0, "cwd": cwd, "kind": "interactive", "startedAt": int((end - 3600 * (i + 1)) * 1000),
                           "sessionId": sid, "name": run_name,
                           "status": "busy" if run == "busy" else "idle"})
            if run == "waiting":
                os.makedirs(os.path.join(data, "waiting"), exist_ok=True)
                with open(os.path.join(data, "waiting", sid + ".json"), "w") as f:
                    json.dump({"at": now, "message": "Claude needs your permission to use Bash"}, f)
    # an empty chat, for the cleanup menu
    empty = os.path.join(projects, os.listdir(projects)[0], str(uuid.UUID(int=rnd.getrandbits(128))) + ".jsonl")
    open(empty, "w").close(); os.utime(empty, (now - 9 * 86400, now - 9 * 86400))

    os.makedirs(os.path.join(data, "status"), exist_ok=True)
    limits = {"five_hour": {"used_percentage": 62, "resets_at": int(now + 2 * 3600)},
              "seven_day": {"used_percentage": 41, "resets_at": int(now + 3 * 86400)}}
    with open(os.path.join(data, "status", agents[0]["sessionId"] + ".json"), "w") as f:
        json.dump({"rate_limits": limits, "cost": {"total_cost_usd": 12.4}}, f)
    with open(os.path.join(data, "limits-history.jsonl"), "w") as f:   # an hour of history, for the forecast
        for k in range(7):
            at = now - 3600 + k * 600
            _line(f, {"t": at, "w": "five_hour", "p": 40 + k * 3, "r": limits["five_hour"]["resets_at"]})
            _line(f, {"t": at, "w": "seven_day", "p": 39 + k * 0.3, "r": limits["seven_day"]["resets_at"]})
    with open(os.path.join(data, "demo-agents.json"), "w") as f: json.dump(agents, f)
    return data
