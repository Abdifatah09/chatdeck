#!/bin/sh
# Install the `chatdeck` command from this folder without pip: puts a small launcher in
# ~/.local/bin that runs chatdeck straight from here (so `git pull` updates it).
# With pip available, `pipx install .` does the same job.
set -e
src="$(cd "$(dirname "$0")" && pwd)/src"
bin="$HOME/.local/bin"
mkdir -p "$bin"
cat > "$bin/chatdeck" <<LAUNCHER
#!/bin/sh
PYTHONPATH="$src\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m chatdeck "\$@"
LAUNCHER
chmod +x "$bin/chatdeck"
echo "Installed $bin/chatdeck"
case ":$PATH:" in *":$bin:"*) ;; *) echo "Add $bin to your PATH to run it as 'chatdeck'." ;; esac
echo "Next: chatdeck setup   (hooks for plan limits and 'waiting for you')"
