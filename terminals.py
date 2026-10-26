"""Opening and finding terminals, per platform.

launch(cwd, command) runs a command in a new terminal tab or window.
focus(pid, name) brings the terminal a running Claude process lives in to the front.
Supported: WSL with Windows Terminal, macOS (Terminal and iTerm2), and Linux desktops
(gnome-terminal, konsole, kitty, alacritty, wezterm, xfce4-terminal, xterm; focusing needs
wmctrl or xdotool). Set CHATDECK_TERMINAL to pick a terminal by name. When nothing
works, callers fall back to giving the person the command to paste."""
import glob, os, platform, secrets, shlex, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
CHOICE = os.environ.get("CHATDECK_TERMINAL", "")
DEVNULL = subprocess.DEVNULL


def kind():
    if platform.system() == "Darwin": return "mac"
    if os.environ.get("WSL_DISTRO_NAME") or "microsoft" in platform.release().lower(): return "wsl"
    if platform.system() == "Linux": return "linux"
    return None


def _spawn(args, **kw):
    try:
        subprocess.Popen(args, stdout=DEVNULL, stderr=DEVNULL, stdin=DEVNULL, start_new_session=True, **kw)
        return True
    except OSError:
        return False


def _shell_line(cwd, command):
    """cd into the folder, run the command, then leave an interactive shell open."""
    shell = os.environ.get("SHELL") or "/bin/bash"
    return f"cd {shlex.quote(cwd)} && {command}; exec {shlex.quote(shell)} -l"


def tty_of(pid):
    """The terminal device a process is attached to, e.g. /dev/pts/3 or /dev/ttys004."""
    try:
        t = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not t or t in ("?", "??"): return None
    return t if t.startswith("/dev/") else "/dev/" + t


def _set_title(tty, title):
    with open(tty, "w") as f: f.write(f"\033]0;{title}\007")


def _tagged(pid, name, find):
    """Give the process's terminal tab a unique title, call find(title), then give it its
    name back. Titles alone can't tell chats apart (several tabs can share a folder name),
    so this identifies the exact tab."""
    tty = tty_of(pid)
    if not tty: return False
    mark = "chatdeck-" + secrets.token_hex(4)
    try:
        _set_title(tty, mark)
        return find(mark)
    except OSError:
        return False
    finally:
        try: _set_title(tty, "✳ " + name if name else "Claude Code")   # Claude Code's own idle title
        except OSError: pass


# --- WSL + Windows Terminal -------------------------------------------------------------

def _win_exe(name, *fallbacks):
    p = shutil.which(name)
    if p: return p
    for pattern in fallbacks:
        hits = glob.glob(pattern)
        if hits: return hits[0]
    return None


def _wsl_launch(cwd, command):
    wt = _win_exe("wt.exe", "/mnt/c/Users/*/AppData/Local/Microsoft/WindowsApps/wt.exe")
    if not wt: return False
    distro = os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
    return _spawn([wt, "-w", "0", "new-tab", "wsl.exe", "-d", distro, "--cd", cwd, "--", "bash", "-lic", command], cwd="/mnt/c")


def _wsl_focus(pid, name):
    ps = _win_exe("powershell.exe", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if not ps: return False
    with open(os.path.join(HERE, "focus_wt.ps1")) as f: script = f.read()
    def find(mark):
        # Optional: VirtualDesktopLib.cs (github.com/MScholtes/VirtualDesktop) lets it switch to a
        # window on another virtual desktop reliably. Give its Windows path in CHATDECK_VDLIB.
        env = dict(os.environ, DASH_MARK=mark, DASH_VDLIB=os.environ.get("CHATDECK_VDLIB", ""),
                   WSLENV="DASH_MARK:DASH_VDLIB")
        try:
            r = subprocess.run([ps, "-NoProfile", "-Command", script], capture_output=True, text=True,
                               cwd="/mnt/c", env=env, timeout=30)
        except subprocess.TimeoutExpired:
            return False
        return r.stdout.strip().endswith("ok")
    return _tagged(pid, name, find)


# --- macOS --------------------------------------------------------------------------------

def _osa(*lines, args=()):
    try:
        r = subprocess.run(["osascript", *sum((["-e", l] for l in lines), []), *args], capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def _mac_app():
    if CHOICE: return "iTerm" if CHOICE.lower().startswith("iterm") else "Terminal"
    return "iTerm" if os.environ.get("TERM_PROGRAM") == "iTerm.app" else "Terminal"


def _mac_launch(cwd, command):
    line = _shell_line(cwd, command)
    if _mac_app() == "iTerm":
        ok, _ = _osa("on run argv", 'tell application "iTerm"', "activate",
                     "set w to (create window with default profile)",
                     "tell current session of w to write text (item 1 of argv)", "end tell", "end run", args=[line])
    else:
        ok, _ = _osa("on run argv", 'tell application "Terminal"', "activate", "do script (item 1 of argv)",
                     "end tell", "end run", args=[line])
    return ok


def _mac_focus(pid, name):
    tty = tty_of(pid)
    if not tty: return False
    # Both apps can report each tab's tty, so no title trick is needed.
    ok, out = _osa("on run argv", 'if application "Terminal" is running then', 'tell application "Terminal"',
                   "repeat with w in windows", "repeat with t in tabs of w",
                   "if tty of t is (item 1 of argv) then",
                   "set selected of t to true", "set index of w to 1", "activate", 'return "ok"',
                   "end if", "end repeat", "end repeat", "end tell", "end if", "end run", args=[tty])
    if out == "ok": return True
    ok, out = _osa("on run argv", 'if application "iTerm" is running then', 'tell application "iTerm"',
                   "repeat with w in windows", "repeat with t in tabs of w", "repeat with s in sessions of t",
                   "if tty of s is (item 1 of argv) then",
                   "select w", "select t", "select s", "activate", 'return "ok"',
                   "end if", "end repeat", "end repeat", "end repeat", "end tell", "end if", "end run", args=[tty])
    return out == "ok"


# --- Linux desktops -----------------------------------------------------------------------

LINUX_TERMINALS = {   # name -> args before the command (cwd is handled by the shell line)
    "gnome-terminal": ["--"], "konsole": ["-e"], "kitty": [], "alacritty": ["-e"], "wezterm": ["start", "--"],
    "xfce4-terminal": ["-x"], "tilix": ["-e"], "foot": [], "x-terminal-emulator": ["-e"], "xterm": ["-e"],
}


def _linux_launch(cwd, command):
    names = [CHOICE] if CHOICE else list(LINUX_TERMINALS)
    for n in names:
        exe = shutil.which(n)
        if exe:
            return _spawn([exe, *LINUX_TERMINALS.get(n, ["-e"]), "bash", "-lc", _shell_line(cwd, command)])
    return False


def _linux_focus(pid, name):
    def find(mark):
        for args in (["wmctrl", "-a", mark], ["xdotool", "search", "--name", mark, "windowactivate"]):
            if shutil.which(args[0]):
                for _ in range(6):   # the terminal takes a moment to pick up the new title
                    if subprocess.run(args, stdout=DEVNULL, stderr=DEVNULL).returncode == 0: return True
                    subprocess.run(["sleep", "0.25"])
        return False
    return _tagged(pid, name, find)


def launch(cwd, command):
    k = kind()
    if k == "wsl": return _wsl_launch(cwd, command)
    if k == "mac": return _mac_launch(cwd, command)
    if k == "linux": return _linux_launch(cwd, command)
    return False


def focus(pid, name):
    k = kind()
    try:
        if k == "wsl": return _wsl_focus(pid, name)
        if k == "mac": return _mac_focus(pid, name)
        if k == "linux": return _linux_focus(pid, name)
    except OSError:
        pass
    return False
