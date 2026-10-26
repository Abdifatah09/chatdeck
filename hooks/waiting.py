#!/usr/bin/env python3
"""Claude Code Notification hook: records that a chat is waiting on you (permission prompt or a
question), so the dashboard can flag it. The dashboard treats the mark as cleared as soon as the
chat's transcript moves on, so nothing has to remove it.

settings.json:  "Notification": [{"matcher": "permission_prompt|elicitation_dialog",
                 "hooks": [{"type": "command", "command": "python3 /path/to/hooks/waiting.py", "async": true}]}]"""
import json, os, re, sys, time

try:
    d = json.load(sys.stdin)
    sid = d.get("session_id", "")
    if re.fullmatch(r"[0-9a-f-]{36}", sid):
        out = os.path.join(os.environ.get("CHATDECK_DIR") or os.path.expanduser("~/.chatdeck"), "waiting")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, sid + ".json.tmp"), "w") as f:
            json.dump({"at": time.time(), "message": d.get("message", ""), "type": d.get("notification_type", "")}, f)
        os.replace(os.path.join(out, sid + ".json.tmp"), os.path.join(out, sid + ".json"))
except Exception:
    pass
