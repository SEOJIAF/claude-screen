"""Claude Code integration: hooks that let agent-shield follow Claude by itself.

`agent-shield install` adds these hooks to ~/.claude/settings.json:

  SessionStart      -> agent-shield hook session-start
                       registers the session, starts the guard, and tells
                       Claude about its private desktop (printed text becomes
                       part of Claude's context)
  UserPromptSubmit  -> agent-shield hook busy    you sent a prompt: Claude works
  Stop              -> agent-shield hook idle    Claude finished its answer
  Notification      -> agent-shield hook idle    Claude waits for your approval
  SessionEnd        -> agent-shield hook end     the session is gone

Each session is one small file in /run/user/UID/agent-shield-claude/, which
the guard (guard.py) reads. A hook never fails Claude: errors are logged and
ignored.
"""

import copy
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_shield.runner import Fail, Runner, log
from agent_shield.session import GUARD_UNIT
from agent_shield.system import PACKAGE_ROOT

PROGRAM = PACKAGE_ROOT / "bin" / "agent-shield"

# Event -> (matcher, argument for `agent-shield hook`).
HOOKS = {
    "SessionStart": (None, "session-start"),
    "UserPromptSubmit": (None, "busy"),
    "Stop": (None, "idle"),
    "Notification": ("permission_prompt|idle_prompt", "idle"),
    "SessionEnd": (None, "end"),
}

INSTRUCTIONS = """\
agent-shield: you have a private desktop (headless Sway, 1920x1080) for tasks \
that need a GUI. The user can't see it and it is separate from their screen. \
It starts by itself the first time you use it. Use it only when a task needs a GUI:
- see it: `agent-shield shot` prints the path of a PNG; read that file
- start an app: `agent-shield run -- swaymsg exec konsole` (Firefox: \
`mkdir -p ~/.cache/agent-firefox`, then `agent-shield run -- swaymsg exec \
'firefox --no-remote --profile ~/.cache/agent-firefox'`)
- type: `agent-shield run -- wtype -s 200 'some text'` (always keep -s 200); \
Enter: `agent-shield run -- wtype -s 200 -k Return`; Ctrl+L: \
`agent-shield run -- wtype -s 200 -M ctrl -k l -m ctrl`
- click at x,y: `agent-shield run -- swaymsg -q seat seat0 cursor set X Y`, \
then `agent-shield run -- swaymsg -q seat seat0 cursor press button1` and \
`... cursor release button1`
- list windows: `agent-shield run -- swaymsg -t get_tree`
Take a screenshot after each action to check it worked. Never run \
`agent-shield stop`, `start`, `install` or `uninstall`."""


# --- the session files ------------------------------------------------------

@dataclass
class ClaudeSession:
    id: str
    pid: int
    busy: bool


def sessions_dir() -> Path:
    return Path(f"/run/user/{os.getuid()}/agent-shield-claude")


def file_name(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:100] or "unknown"


def save_session(session_id: str, pid: int, busy: bool) -> None:
    folder = sessions_dir()
    folder.mkdir(mode=0o700, exist_ok=True)
    name = file_name(session_id)
    temporary = folder / f".{name}.tmp"
    temporary.write_text(json.dumps({"pid": pid, "busy": busy}))
    temporary.replace(folder / name)  # atomic: the guard never reads half a file


def remove_session(session_id: str) -> None:
    (sessions_dir() / file_name(session_id)).unlink(missing_ok=True)


def live_sessions() -> list[ClaudeSession]:
    """The registered sessions whose Claude process still runs (others are cleaned up)."""
    sessions = []
    for path in sessions_dir().glob("[!.]*"):
        try:
            data = json.loads(path.read_text())
            pid, busy = int(data["pid"]), bool(data["busy"])
        except (OSError, ValueError, KeyError):
            continue
        if Path(f"/proc/{pid}").exists():
            sessions.append(ClaudeSession(path.name, pid, busy))
        else:  # Claude crashed or was killed without SessionEnd
            path.unlink(missing_ok=True)
    return sessions


def parent_pid(pid: int) -> int:
    # /proc/PID/stat is "PID (name) STATE PPID ..."; the name may contain spaces.
    stat = Path(f"/proc/{pid}/stat").read_text()
    return int(stat.rsplit(")", 1)[1].split()[1])


def claude_pid() -> int:
    """The Claude Code process that ran this hook: the nearest ancestor called claude."""
    pid = fallback = os.getppid()
    for _ in range(6):
        try:
            if Path(f"/proc/{pid}/comm").read_text().strip() == "claude":
                return pid
            pid = parent_pid(pid)
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1:
            break
    return fallback


# --- the hooks ----------------------------------------------------------------

def handle_hook(event: str) -> int:
    """Run one hook. Always exits 0, so a problem here never gets in Claude's way."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        data = {}
    try:
        session_id = str(data.get("session_id", ""))
        if event == "end":
            remove_session(session_id)
        else:
            save_session(session_id, claude_pid(), busy=(event == "busy"))
            if event in ("session-start", "busy"):
                subprocess.run(["systemctl", "--user", "start", "--no-block", GUARD_UNIT],
                               capture_output=True, timeout=5)
    except Exception as error:  # noqa: BLE001 - a hook must never break Claude
        log.warning("hook %s failed: %s", event, error)
    if event == "session-start":
        print(INSTRUCTIONS)
    return 0


# --- ~/.claude/settings.json ---------------------------------------------------

def settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def is_ours(hook: dict) -> bool:
    return hook.get("command", "").endswith("agent-shield") and hook.get("args", [""])[0] == "hook"


def without_hooks(settings: dict) -> dict:
    """A copy of settings with every agent-shield hook removed and nothing else changed."""
    settings = copy.deepcopy(settings)
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        kept = []
        for entry in hooks[event]:
            others = [hook for hook in entry.get("hooks", []) if not is_ours(hook)]
            if others or not entry.get("hooks"):
                kept.append({**entry, "hooks": others})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if "hooks" in settings and not hooks:
        del settings["hooks"]
    return settings


def with_hooks(settings: dict, program: str) -> dict:
    """A copy of settings with agent-shield's hooks added after any hooks already there."""
    settings = without_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    for event, (matcher, argument) in HOOKS.items():
        entry = {"hooks": [{"type": "command", "command": program,
                            "args": ["hook", argument], "timeout": 10}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        hooks.setdefault(event, []).append(entry)
    return settings


def read_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        raise Fail(f"{path} is not valid JSON; fix it first, then run this again") from None


def hooks_installed() -> bool:
    try:
        return with_hooks(read_settings(), str(PROGRAM)) == read_settings()
    except Fail:
        return False


def write_settings(runner: Runner, settings: dict) -> None:
    path = settings_path()
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    runner.write(path, json.dumps(settings, indent=2) + "\n", mode)


def install_hooks(runner: Runner) -> None:
    current = read_settings()
    updated = with_hooks(current, str(PROGRAM))
    if updated == current:
        print(f"Claude Code hooks are already in {settings_path()}.")
        return
    write_settings(runner, updated)
    print(f"Added agent-shield's hooks to {settings_path()} (your other settings are unchanged).")


def remove_hooks(runner: Runner) -> None:
    current = read_settings()
    updated = without_hooks(current)
    if updated != current:
        write_settings(runner, updated)
        print(f"Removed agent-shield's hooks from {settings_path()}.")
