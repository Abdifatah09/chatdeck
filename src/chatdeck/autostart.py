"""Starting chatdeck at login.

WSL: a "chatdeck" shortcut in the Windows Startup folder runs the launcher (server in WSL, then
the corner bubble and tray icon); another in the Start menu also opens the dashboard.
macOS: a LaunchAgent. Linux: a systemd user service, or an XDG autostart entry without systemd."""
import os, shutil, subprocess

from . import config, desktop, hooks, terminals

LABEL = "io.github.chatdeck"
MAC_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")
SYSTEMD_UNIT = os.path.expanduser("~/.config/systemd/user/chatdeck.service")
XDG_ENTRY = os.path.expanduser("~/.config/autostart/chatdeck.desktop")


def _serve_args(port):
    """The command line that runs the server, as a list (for launchd/systemd)."""
    exe = shutil.which("chatdeck")
    if exe: return [exe, "serve", "--port", str(port)]
    return None   # running from a clone without the installed command: use the shell form


def _shell_cmd(port):
    return f"{hooks.command()} serve --port {port}"


def enable(port, hotkey=""):
    k = terminals.kind()
    if k == "wsl":
        distro = os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"
        folder = desktop.install_windows_files(port, distro, hooks.command())
        f = desktop.win_folders()
        desktop.shortcut(f["startup"] + "\\chatdeck.lnk", folder)
        desktop.shortcut(f["programs"] + "\\chatdeck.lnk", folder, "-Open", hotkey)
        return [f"Windows Startup: starts chatdeck and the corner button at login",
                f"Start menu: \"chatdeck\" opens the dashboard" + (f" ({hotkey})" if hotkey else "")]
    if k == "mac":
        args = _serve_args(port) or ["/bin/sh", "-lc", _shell_cmd(port)]
        items = "".join(f"<string>{a}</string>" for a in args)
        os.makedirs(os.path.dirname(MAC_PLIST), exist_ok=True)
        with open(MAC_PLIST, "w") as fh:
            fh.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                     f'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
                     f"<key>Label</key><string>{LABEL}</string><key>ProgramArguments</key><array>{items}</array>"
                     f"<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>"
                     f"<key>StandardErrorPath</key><string>{config.DATA}/server.log</string></dict></plist>\n")
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", MAC_PLIST], capture_output=True)
        return ["LaunchAgent: chatdeck starts at login"]
    if k == "linux":
        if shutil.which("systemctl") and subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True).returncode in (0, 1):
            exec_line = " ".join(_serve_args(port) or ["/bin/sh", "-lc", f"'{_shell_cmd(port)}'"])
            os.makedirs(os.path.dirname(SYSTEMD_UNIT), exist_ok=True)
            with open(SYSTEMD_UNIT, "w") as fh:
                fh.write(f"[Unit]\nDescription=chatdeck dashboard for Claude Code\n\n[Service]\nExecStart={exec_line}\n"
                         f"Restart=on-failure\n\n[Install]\nWantedBy=default.target\n")
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", "chatdeck.service"], capture_output=True)
            return ["systemd user service: chatdeck starts at login"]
        os.makedirs(os.path.dirname(XDG_ENTRY), exist_ok=True)
        with open(XDG_ENTRY, "w") as fh:
            fh.write(f"[Desktop Entry]\nType=Application\nName=chatdeck\nExec=/bin/sh -lc '{_shell_cmd(port)}'\n"
                     f"X-GNOME-Autostart-enabled=true\n")
        return ["desktop autostart entry: chatdeck starts when you log in"]
    raise SystemExit("Starting at login isn't supported on this system.")


def disable():
    k = terminals.kind(); done = []
    if k == "wsl":
        f = desktop.win_folders()
        for lnk in (f["startup"] + "\\chatdeck.lnk", f["programs"] + "\\chatdeck.lnk"):
            p = desktop.to_wsl(lnk)
            if os.path.exists(p): os.remove(p); done.append(f"removed {lnk}")
    elif k == "mac":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        if os.path.exists(MAC_PLIST): os.remove(MAC_PLIST); done.append("removed the LaunchAgent")
    elif k == "linux":
        if os.path.exists(SYSTEMD_UNIT):
            subprocess.run(["systemctl", "--user", "disable", "--now", "chatdeck.service"], capture_output=True)
            os.remove(SYSTEMD_UNIT); done.append("removed the systemd user service")
        if os.path.exists(XDG_ENTRY): os.remove(XDG_ENTRY); done.append("removed the autostart entry")
    return done


def status():
    k = terminals.kind()
    if k == "wsl": return os.path.exists(desktop.to_wsl(desktop.win_folders()["startup"] + "\\chatdeck.lnk"))
    if k == "mac": return os.path.exists(MAC_PLIST)
    if k == "linux": return os.path.exists(SYSTEMD_UNIT) or os.path.exists(XDG_ENTRY)
    return False
