"""Keeping the laptop awake with the lid closed.

Two mechanisms, because two different programs may react to the lid:

1. The sleep inhibitor (agent-shield-inhibit.service, no root needed) takes
   logind "block" locks on sleep, idle and handle-lid-switch while the agent runs.
   - handle-lid-switch: logind skips its own lid action.
   - sleep: logind refuses suspend requests from others, e.g. from KDE's
     PowerDevil, which takes over the lid on Plasma and asks logind to suspend.
2. The optional logind drop-in (needs sudo) sets HandleLidSwitch*=ignore for
   good, until `agent-shield uninstall`.
"""

import json
from pathlib import Path

from agent_shield.runner import Runner, capture, succeeds
from agent_shield.session import render

DROPIN = Path("/etc/systemd/logind.conf.d/99-agent-shield.conf")
BACKUP = DROPIN.with_name(DROPIN.name + ".backup-agent-shield")
HEADER = "# Installed by agent-shield."

LOGIND = ["org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager"]
LID_SETTINGS = ("HandleLidSwitch", "HandleLidSwitchExternalPower", "HandleLidSwitchDocked")


def lid_settings() -> dict[str, str]:
    """What logind does when the lid closes, e.g. {"HandleLidSwitch": "suspend", ...}."""
    settings = {}
    for name in LID_SETTINGS:
        out = capture(["busctl", "get-property", *LOGIND, name])  # prints: s "suspend"
        settings[name] = out[3:-1] if out.startswith('s "') else "?"
    return settings


def inhibitors() -> list[dict]:
    """logind's current inhibitor locks, as dicts with what/who/why/mode/uid/pid."""
    out = capture(["busctl", "--json=short", "call", *LOGIND, "ListInhibitors"])
    try:
        rows = json.loads(out)["data"][0]
    except (ValueError, KeyError, IndexError):
        return []
    keys = ("what", "who", "why", "mode", "uid", "pid")
    return [dict(zip(keys, row)) for row in rows]


def lid_handlers() -> list[str]:
    """Programs that took the lid switch over from logind (e.g. PowerDevil on KDE)."""
    return sorted({lock["who"] for lock in inhibitors()
                   if "handle-lid-switch" in lock["what"].split(":")
                   and lock["who"] != "agent-shield"})


def our_inhibitor_held() -> bool:
    return any(lock["who"] == "agent-shield" and lock["mode"] == "block" for lock in inhibitors())


def can_suspend() -> str:
    """logind's answer to "may I suspend now?": "yes", "challenge" (blocked), "no", "na"."""
    out = capture(["busctl", "call", *LOGIND, "CanSuspend"])
    return out[3:-1] if out.startswith('s "') else "?"


def can_inhibit() -> bool:
    """Does polkit let you take the locks the inhibitor needs? (Takes them for an instant.)"""
    return succeeds(["systemd-inhibit", "--what=sleep:idle:handle-lid-switch", "--mode=block",
                     "--who=agent-shield-doctor", "--why=permission check", "true"])


def read_dropin() -> str | None:
    try:
        return DROPIN.read_text()
    except OSError:
        return None


def dropin_installed() -> bool:
    return (read_dropin() or "").startswith(HEADER)


def install_dropin(runner: Runner, state: dict, lid_owner: list[str]) -> None:
    content = render("99-agent-shield.conf")
    current = read_dropin()
    if current == content:
        print(f"The logind drop-in is already installed ({DROPIN}).")
        return
    print(f"\nOptional: logind drop-in (needs sudo)\n"
          f"  Writes {DROPIN} so systemd-logind ignores the lid\n"
          f"  (HandleLidSwitch, HandleLidSwitchExternalPower and HandleLidSwitchDocked = ignore).\n"
          f"  - It stays until `agent-shield uninstall`, even when no agent is running.\n"
          f"  - While the agent runs, the sleep inhibitor already handles the lid without it.")
    if lid_owner:
        print(f"  - Here {', '.join(lid_owner)} handles the lid instead of logind, so this file\n"
              f"    only matters when that program isn't running.")
    if not runner.confirm(f"Install {DROPIN} with sudo?"):
        print("Skipped the logind drop-in.")
        return
    created_dir = not DROPIN.parent.exists()
    backed_up = current is not None and not current.startswith(HEADER)
    if backed_up:  # a file of the same name that isn't ours: keep a copy
        runner.sudo(["cp", "-a", str(DROPIN), str(BACKUP)])
    runner.sudo(["mkdir", "-p", str(DROPIN.parent)])
    runner.sudo(["tee", str(DROPIN)], input=content, capture=True)  # capture: don't echo it
    runner.sudo(["chmod", "0644", str(DROPIN)])
    runner.sudo(["systemctl", "reload", "systemd-logind"])  # applies it without a restart
    state["logind"] = {"created_dir": created_dir, "backed_up": backed_up}


def remove_dropin(runner: Runner, state: dict) -> None:
    current = read_dropin()
    if current is None:
        return
    if not current.startswith(HEADER):
        print(f"Leaving {DROPIN} alone: agent-shield did not write it.")
        return
    if not runner.confirm(f"Remove {DROPIN} with sudo (lid handling goes back to normal)?"):
        print(f"Kept {DROPIN}. Remove it later with: sudo rm {DROPIN}")
        return
    info = state.get("logind", {})
    runner.sudo(["rm", "-f", str(DROPIN)])
    if info.get("backed_up") and BACKUP.exists():
        runner.sudo(["mv", str(BACKUP), str(DROPIN)])
    elif info.get("created_dir"):
        runner.sudo(["rmdir", "--ignore-fail-on-non-empty", str(DROPIN.parent)])
    runner.sudo(["systemctl", "reload", "systemd-logind"])
    state.pop("logind", None)
