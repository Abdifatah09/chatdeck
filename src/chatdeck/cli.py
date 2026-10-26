"""chatdeck command line.

  chatdeck                  start the dashboard (in the background) and open it
  chatdeck serve            run the server in the foreground (what autostart uses)
  chatdeck stop             stop a background server
  chatdeck setup            add the Claude Code hooks for plan limits and "waiting for you"
  chatdeck autostart on|off start chatdeck at login, or stop doing that
  chatdeck uninstall        remove the hooks and autostart
  chatdeck demo             try it with made-up chats (nothing real is read or changed)
  chatdeck bubble           Windows (WSL): start the corner button and tray icon
  chatdeck hook ...         used by Claude Code itself"""
import argparse, os, signal, subprocess, sys, time, urllib.request

from . import __version__, config, hooks


def _up(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            return b"<title>chatdeck</title>" in r.read(4096)
    except OSError:
        return False


def _start_background(port):
    os.makedirs(config.DATA, exist_ok=True)
    log = open(os.path.join(config.DATA, "server.log"), "ab")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                                    os.environ.get("PYTHONPATH")])))
    subprocess.Popen([sys.executable, "-m", "chatdeck", "serve", "--port", str(port)], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    for _ in range(40):
        if _up(port): return True
        time.sleep(0.25)
    return False


def cmd_open(a):
    from . import desktop
    if not _up(a.port):
        print(f"Starting chatdeck on http://127.0.0.1:{a.port} ...")
        if not _start_background(a.port):
            sys.exit(f"chatdeck didn't start - see {os.path.join(config.DATA, 'server.log')}")
    if not a.no_open: desktop.open_dashboard(f"http://127.0.0.1:{a.port}/")
    print(f"chatdeck is running at http://127.0.0.1:{a.port}")
    if not os.path.exists(os.path.join(config.DATA, "status")) and "statusline_command" not in config.load():
        print("Tip: run `chatdeck setup` so it can show your plan limits and which chats are waiting for you.")


def cmd_serve(a):
    from . import server
    if _up(a.port): sys.exit(f"chatdeck is already running on port {a.port}.")
    print(f"chatdeck on http://127.0.0.1:{a.port}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve(a.port)
    except OSError as e:
        sys.exit(f"Can't use port {a.port}: {e.strerror}. Pick another with --port.")
    except KeyboardInterrupt:
        pass


def cmd_stop(a):
    try:
        with open(os.path.join(config.DATA, "server.pid")) as f: pid = int(f.read())
        os.kill(pid, signal.SIGTERM); print("Stopped chatdeck.")
    except (OSError, ValueError):
        print("chatdeck isn't running in the background.")


def cmd_setup(a):
    for line in hooks.install(): print("  " + line)
    print(f"Done. Your previous settings were backed up to {os.path.join(config.DATA, 'backups')}.")
    print("New and restarted Claude Code sessions pick this up.")


def cmd_uninstall(a):
    done = hooks.uninstall()
    try:
        from . import autostart
        done += autostart.disable()
    except SystemExit:
        pass
    for line in done or ["nothing to remove"]: print("  " + line)


def cmd_autostart(a):
    from . import autostart
    if a.state == "on":
        cfg = config.load(); cfg["port"] = a.port; config.save(cfg)
        for line in autostart.enable(a.port, a.hotkey): print("  " + line)
    elif a.state == "off":
        for line in autostart.disable() or ["it wasn't set to start at login"]: print("  " + line)
    else:
        print("on" if autostart.status() else "off")


def cmd_bubble(a):
    from . import desktop, terminals
    if terminals.kind() != "wsl": sys.exit("The corner button is Windows-only for now (run chatdeck from WSL).")
    desktop.start_bubble(a.port)


def cmd_demo(a):
    import shutil, tempfile
    from . import demo, desktop
    root = tempfile.mkdtemp(prefix="chatdeck-demo-")
    try:
        env = dict(os.environ, HOME=root, CHATDECK_DIR=demo.build(root), CHATDECK_DEMO="1",
                   PYTHONPATH=os.pathsep.join(filter(None, [os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                            os.environ.get("PYTHONPATH")])))
        srv = subprocess.Popen([sys.executable, "-m", "chatdeck", "serve", "--port", str(a.port)], env=env)
        url = f"http://127.0.0.1:{a.port}/"
        for _ in range(40):
            if _up(a.port) or srv.poll() is not None: break
            time.sleep(0.25)
        if not a.no_open and srv.poll() is None: desktop.open_dashboard(url)
        srv.wait()
    except KeyboardInterrupt:
        srv.wait()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cmd_hook(a):
    if a.name == "statusline": hooks.statusline()
    elif a.name == "waiting": hooks.waiting()


def main(argv=None):
    p = argparse.ArgumentParser(prog="chatdeck", description="A local dashboard for your Claude Code chats.")
    p.add_argument("--version", action="version", version=f"chatdeck {__version__}")
    sub = p.add_subparsers(dest="cmd")
    def port_arg(sp): sp.add_argument("--port", type=int, default=config.port(), help=f"default {config.port()}")
    o = sub.add_parser("open", help="start the dashboard if needed and open it (the default)")
    port_arg(o); o.add_argument("--no-open", action="store_true", help="start it but don't open a window")
    port_arg(sub.add_parser("serve", help="run the server in the foreground"))
    sub.add_parser("stop", help="stop a background server")
    sub.add_parser("setup", help="add the Claude Code hooks chatdeck uses")
    sub.add_parser("uninstall", help="remove the hooks and autostart")
    s = sub.add_parser("autostart", help="start chatdeck at login: on, off or status")
    s.add_argument("state", choices=["on", "off", "status"]); port_arg(s)
    s.add_argument("--hotkey", default="", help='Windows: shortcut key for the Start menu entry, e.g. "Ctrl+Alt+A"')
    d = sub.add_parser("demo", help="try chatdeck with made-up chats")
    d.add_argument("--port", type=int, default=47199, help="default 47199"); d.add_argument("--no-open", action="store_true")
    port_arg(sub.add_parser("bubble", help="Windows: start the corner button and tray icon"))
    h = sub.add_parser("hook", help=argparse.SUPPRESS); h.add_argument("name", choices=["statusline", "waiting"])
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "open")   # plain `chatdeck` (with or without options) starts and opens the dashboard
    a = p.parse_args(argv)
    {"open": cmd_open, "serve": cmd_serve, "stop": cmd_stop, "setup": cmd_setup, "uninstall": cmd_uninstall,
     "autostart": cmd_autostart, "demo": cmd_demo, "bubble": cmd_bubble, "hook": cmd_hook}[a.cmd](a)
