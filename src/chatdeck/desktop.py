"""Opening the dashboard window, and the Windows side of things when running under WSL
(the corner bubble, shortcuts in the Start menu and Startup folder)."""
import filecmp, os, shutil, subprocess, webbrowser

from . import terminals

PKG = os.path.dirname(os.path.abspath(__file__))
WIN_FILES = ("bubble.ps1", "start.ps1")


# --- opening the dashboard ------------------------------------------------------------------

def _app_browser():
    """A Chromium browser that can open the dashboard as its own app window."""
    k = terminals.kind()
    if k == "wsl":
        for p in ("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                  "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                  "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                  "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe"):
            if os.path.exists(p): return [p]
    elif k == "mac":
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"):
            if os.path.isdir(f"/Applications/{app}.app"): return ["open", "-na", app, "--args"]
    else:
        for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
            if shutil.which(n): return [shutil.which(n)]
    return None


def open_dashboard(url):
    b = _app_browser()
    if b:
        try:
            subprocess.Popen([*b, f"--app={url}", "--window-size=520,900"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, cwd="/mnt/c" if b[0].startswith("/mnt/") else None)
            return
        except OSError:
            pass
    webbrowser.open(url)


# --- Windows (from WSL) ---------------------------------------------------------------------

def _ps(script, timeout=30):
    ps = terminals._win_exe("powershell.exe", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    r = subprocess.run([ps, "-NoProfile", "-Command", script], capture_output=True, text=True, cwd="/mnt/c", timeout=timeout)
    return r.stdout.strip()


def win_folders():
    """{"local": ..., "startup": ..., "programs": ...} as Windows paths."""
    out = _ps('[Environment]::GetFolderPath("LocalApplicationData"); [Environment]::GetFolderPath("Startup"); '
              '[Environment]::GetFolderPath("Programs")')
    local, startup, programs = out.splitlines()[:3]
    return {"local": local, "startup": startup, "programs": programs}


def to_wsl(win_path):
    return subprocess.run(["wslpath", "-u", win_path], capture_output=True, text=True).stdout.strip()


def install_windows_files(port, distro, cmd):
    """Copy the bubble and launcher into %LOCALAPPDATA%\\chatdeck; returns that folder (Windows path)."""
    folder = win_folders()["local"] + "\\chatdeck"
    dest = to_wsl(folder); os.makedirs(dest, exist_ok=True)
    shutil.copy(os.path.join(PKG, "windows", "bubble.ps1"), dest)
    shutil.copy(os.path.join(PKG, "static", "logo.ico"), dest)
    with open(os.path.join(PKG, "windows", "start.ps1")) as f: start = f.read()
    start = (start.replace("__PORT__", str(port)).replace("__DISTRO__", distro).replace("__CMD__", cmd))
    with open(os.path.join(dest, "start.ps1"), "w", newline="\r\n") as f: f.write(start)
    return folder


def shortcut(lnk, target_folder, args="", hotkey=""):
    """Create a .lnk that runs start.ps1 (hidden) from target_folder."""
    script = (f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut("{lnk}"); '
              f'$s.TargetPath = "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"; '
              f'$s.Arguments = \'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{target_folder}\\start.ps1" {args}\'; '
              f'$s.WindowStyle = 7; $s.IconLocation = "{target_folder}\\logo.ico"; $s.Description = "chatdeck"; '
              + (f'$s.Hotkey = "{hotkey}"; ' if hotkey else "") + '$s.Save()')
    _ps(script)


def start_bubble(port):
    """Start the corner bubble (and tray icon). A second start just shows the running one."""
    folder = win_folders()["local"] + "\\chatdeck"
    src, dst = os.path.join(PKG, "windows", "bubble.ps1"), os.path.join(to_wsl(folder), "bubble.ps1")
    if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):   # also after chatdeck is updated
        os.makedirs(to_wsl(folder), exist_ok=True)
        shutil.copy(src, dst)
    _ps(f'Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "-NoProfile","-WindowStyle","Hidden",'
        f'"-ExecutionPolicy","Bypass","-File","{folder}\\bubble.ps1","-Port","{port}"')
