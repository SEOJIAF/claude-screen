"""A global hotkey that toggles the overlay (default Meta+Shift+P).

On KDE Plasma, "custom command" shortcuts run a small .desktop file, and the
shortcut service (kglobalaccel, inside KWin) learns the key over D-Bus. That
is what System Settings > Keyboard > Shortcuts > Add Command does too, so the
shortcut shows up there and you can change or remove it like any other.
On other desktops we print the line to add to their config instead.
"""

import os
from pathlib import Path

from agent_shield.runner import Fail, Runner, capture
from agent_shield.session import render
from agent_shield.system import PACKAGE_ROOT

DEFAULT_KEY = "Meta+Shift+P"
DESKTOP_ID = "net.local.agent-shield-overlay.desktop"
# kglobalaccel names an action with: component, action, and their display names.
ACTION = [DESKTOP_ID, "_launch", "agent-shield", "Toggle the privacy overlay"]
KGLOBALACCEL = ["org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel"]
# kglobalaccel flags: SetPresent (2) marks the action as available, without it
# Plasma stores the key but never listens for it; NoAutoloading (4) means "use
# the key we give, not one saved earlier".
SET_FLAGS = 2 | 4

# Qt's numbers for keys: a combination is the modifiers OR-ed with one key.
MODIFIERS = {"meta": 0x10000000, "ctrl": 0x04000000, "alt": 0x08000000, "shift": 0x02000000}
NAMED_KEYS = {"escape": 0x01000000, "space": 0x20,
              **{f"f{n}": 0x01000030 + n - 1 for n in range(1, 13)}}

PROGRAM = PACKAGE_ROOT / "bin" / "agent-shield"


def key_code(text: str) -> int:
    """Qt's number for a key combination, e.g. "Meta+Shift+P" -> 0x12000050."""
    *modifiers, key = [part.strip() for part in text.split("+")]
    code = 0
    for modifier in modifiers:
        if modifier.lower() not in MODIFIERS:
            raise Fail(f"unknown modifier {modifier!r} in {text!r} (use Meta, Ctrl, Alt, Shift)")
        code |= MODIFIERS[modifier.lower()]
    if len(key) == 1 and key.isalnum():
        return code | ord(key.upper())
    if key.lower() in NAMED_KEYS:
        return code | NAMED_KEYS[key.lower()]
    raise Fail(f"unknown key {key!r} in {text!r} (use a letter, a digit, F1-F12, Escape or Space)")


def key_text(code: int) -> str:
    """The reverse of key_code: 0x12000050 -> "Meta+Shift+P"."""
    parts = [name.capitalize() for name, bit in MODIFIERS.items() if code & bit]
    key = code & 0x01FFFFFF
    names = {value: name.capitalize() for name, value in NAMED_KEYS.items()}
    parts.append(names.get(key, chr(key) if key < 0x110000 else hex(key)))
    return "+".join(parts)


def desktop_file() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data) / "applications" / DESKTOP_ID


def kglobalaccel(*arguments: str) -> list[str]:
    return ["busctl", "--user", "call", *KGLOBALACCEL, *arguments]


def install(runner: Runner, desktop: str, key: str) -> None:
    if desktop != "kde":
        print_instructions(desktop, key)
        return
    code = key_code(key)
    runner.write(desktop_file(), render("agent-shield-overlay.desktop", agent_shield=PROGRAM))
    runner.run(["kbuildsycoca6"], check=False, quiet=True)  # let Plasma find the new file
    runner.run(kglobalaccel("doRegister", "as", "4", *ACTION), quiet=True)
    result = runner.run(kglobalaccel("setShortcutKeys", "asa(ai)u", "4", *ACTION,
                                     "1", "1", str(code), str(SET_FLAGS)), capture=True)
    # kglobalaccel answers with the keys it really set; a key that is taken is left out.
    if not runner.dry_run and str(code) not in result.stdout.decode().split():
        raise Fail(f"{key} is already used by another shortcut; choose another with "
                   f"`agent-shield install --hotkey KEYS`")
    print(f"Hotkey: {key} shows the overlay. Change it in System Settings > Keyboard > Shortcuts.")


def current_key() -> str | None:
    """The key bound to the overlay right now (KDE only), or None."""
    if not desktop_file().exists():
        return None
    out = capture(kglobalaccel("shortcutKeys", "as", "4", *ACTION)).split()
    # busctl prints: a(ai) <number of keys> <length of 1st> <code> ...
    if len(out) >= 4 and out[1] != "0":
        return key_text(int(out[3]))
    return None


def is_listening() -> bool:
    """Is Plasma actually waiting for the key? (A stored key is not enough.)"""
    path = "/component/" + "".join(ch if ch.isalnum() else "_" for ch in DESKTOP_ID)
    out = capture(["busctl", "--user", "call", "org.kde.kglobalaccel", path,
                   "org.kde.kglobalaccel.Component", "isActive"])
    return out == "b true"


def remove(runner: Runner) -> None:
    if not desktop_file().exists():
        return
    runner.run(kglobalaccel("unregister", "ss", DESKTOP_ID, "_launch"), check=False, quiet=True)
    runner.remove(desktop_file())
    runner.run(["kbuildsycoca6"], check=False, quiet=True)


def print_instructions(desktop: str, key: str) -> None:
    command = f"{PROGRAM} overlay"
    lines = {
        "sway": f"add to ~/.config/sway/config:  bindsym Mod4+Shift+p exec {command}",
        "hyprland": f"add to ~/.config/hypr/hyprland.conf:  bind = SUPER SHIFT, P, exec, {command}",
        "gnome": f"Settings > Keyboard > Custom Shortcuts: add the command `{command}`",
    }
    print(f"To toggle the overlay with {key}, {lines.get(desktop, f'bind a key to: {command}')}")
