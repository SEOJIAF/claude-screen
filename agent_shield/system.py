"""Facts about this machine: distro, desktop, programs, screens, sessions.

Nothing in this file changes the system; it only looks.
"""

import importlib.util
import os
import shutil
import time
from pathlib import Path

from agent_shield.runner import capture

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PACKAGE_ROOT / "templates"

DESKTOPS = ("gnome", "kde", "sway", "hyprland")
DESKTOP_NAMES = {"gnome": "GNOME", "kde": "KDE Plasma", "sway": "Sway",
                 "hyprland": "Hyprland", "unknown": "an unknown desktop"}


def detect_desktop(value: str | None = None) -> str:
    """Turn $XDG_CURRENT_DESKTOP (like "KDE" or "ubuntu:GNOME") into one of DESKTOPS."""
    if value is None:
        value = os.environ.get("XDG_CURRENT_DESKTOP", "")
    for part in value.lower().split(":"):
        if part in DESKTOPS:
            return part
    return "unknown"


def real_desktop() -> str:
    """Your real desktop, even when called from inside the agent's environment.

    There $XDG_CURRENT_DESKTOP says "sway", so we ask logind instead which
    desktop your graphical session runs.
    """
    if os.environ.get("AGENT_SHIELD_SESSION"):
        return detect_desktop(session_property(graphical_session_id(), "Desktop"))
    return detect_desktop()


def os_release() -> dict[str, str]:
    """The fields of /etc/os-release, e.g. {"ID": "fedora", "VERSION_ID": "44", ...}."""
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return {}
    return parse_os_release(text)


def parse_os_release(text: str) -> dict[str, str]:
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip().strip('"')
    return fields


def detect_distro(fields: dict[str, str] | None = None) -> str:
    """Return "arch", "debian" (also Ubuntu, Mint...), "fedora" or "unknown"."""
    fields = os_release() if fields is None else fields
    for name in [fields.get("ID", "")] + fields.get("ID_LIKE", "").split():
        if name in ("arch", "debian", "fedora"):
            return name
        if name == "ubuntu":
            return "debian"
    return "unknown"


# Which package provides a program, per distro family: (arch, debian, fedora).
PACKAGES = {
    "sway": ("sway", "sway", "sway"),
    "swaymsg": ("sway", "sway", "sway"),
    "grim": ("grim", "grim", "grim"),
    "wtype": ("wtype", "wtype", "wtype"),
    "wayvnc": ("wayvnc", "wayvnc", "wayvnc"),
    "swaylock": ("swaylock", "swaylock", "swaylock"),
    "hyprlock": ("hyprlock", "hyprlock", "hyprlock"),
    "hyprctl": ("hyprland", "hyprland", "hyprland"),
    "dbus-run-session": ("dbus", "dbus", "dbus-daemon"),
    "ssh-keygen": ("openssh", "openssh-client", "openssh"),
    "tkinter": ("tk", "python3-tk", "python3-tkinter"),
}
INSTALL_COMMAND = {"arch": "sudo pacman -S --needed",
                   "debian": "sudo apt install",
                   "fedora": "sudo dnf install"}


def install_hint(programs: list[str], distro: str) -> str:
    """The command that installs the packages providing `programs` on this distro."""
    if distro not in INSTALL_COMMAND:
        return "install with your package manager: " + " ".join(programs)
    column = ("arch", "debian", "fedora").index(distro)
    packages = sorted({PACKAGES.get(p, (p, p, p))[column] for p in programs})
    return f"{INSTALL_COMMAND[distro]} {' '.join(packages)}"


def has_program(name: str) -> bool:
    if name == "tkinter":
        return importlib.util.find_spec("_tkinter") is not None
    return shutil.which(name) is not None


def connected_screens() -> int:
    """How many monitors are plugged in, counted from the kernel's DRM connectors."""
    count = 0
    for status in Path("/sys/class/drm").glob("card*-*/status"):
        try:
            count += status.read_text().strip() == "connected"
        except OSError:
            pass
    return count


def graphical_session_id() -> str:
    """The logind session of your desktop (works from any terminal, even over SSH)."""
    session = capture(["loginctl", "show-user", str(os.getuid()), "--property=Display", "--value"])
    return session or os.environ.get("XDG_SESSION_ID", "")


def session_property(session: str, name: str) -> str:
    """One property of a logind session, e.g. session_property("2", "LockedHint") -> "no"."""
    if not session:
        return ""
    return capture(["loginctl", "show-session", session, f"--property={name}", "--value"])


def wait_for(check, timeout: float, interval: float = 0.2) -> bool:
    """Call check() until it returns something true, for at most `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
    return bool(check())
