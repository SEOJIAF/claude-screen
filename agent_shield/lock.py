"""Lock your real desktop session, or cover it with the tripwire overlay."""

import os
import shlex
import shutil
import sys

from agent_shield.runner import Fail, Runner, capture
from agent_shield.system import (PACKAGE_ROOT, connected_screens, detect_distro,
                                 has_program, install_hint, session_property, wait_for)

OVERLAY_UNIT = "agent-shield-overlay.service"
# The overlay's looks (the scenes in scenes.py); listed here so that commands
# which don't show the overlay never need tkinter.
THEMES = ("gentoo", "orbit")

# Variables the overlay (and the lock command run after it) need from your desktop.
OVERLAY_ENV = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "HYPRLAND_INSTANCE_SIGNATURE")


def program(runner: Runner, name: str) -> str:
    path = shutil.which(name)
    if path:
        return path
    if runner.dry_run:
        return name
    raise Fail(f"{name} is needed to lock your session but is not installed. "
               f"Install it with: {install_hint([name], detect_distro())}")


def lock_command(runner: Runner, desktop: str, session: str) -> list[str]:
    """The command that locks your real session on this desktop."""
    if desktop == "sway":
        return [program(runner, "swaylock"), "--daemonize"]  # returns once locked
    if desktop == "hyprland":
        program(runner, "hyprlock")
        return [program(runner, "hyprctl"), "dispatch", "exec", "hyprlock"]
    # GNOME, KDE and most other desktops lock through logind.
    if not session:
        if not runner.dry_run:
            raise Fail("could not find your graphical session (loginctl show-user $USER)")
        session = "<session>"
    return [program(runner, "loginctl"), "lock-session", session]


def is_locked(desktop: str, session: str) -> bool:
    if desktop == "sway":
        return bool(capture(["pgrep", "-u", str(os.getuid()), "-x", "swaylock"]))
    if desktop == "hyprland":
        return bool(capture(["pgrep", "-u", str(os.getuid()), "-x", "hyprlock"]))
    return session_property(session, "LockedHint") == "yes"


def lock_now(runner: Runner, desktop: str, session: str) -> None:
    """Lock the real session and check that it really is locked."""
    command = lock_command(runner, desktop, session)
    print("Locking your real session...")
    runner.run(command)
    if runner.dry_run:
        return
    if not wait_for(lambda: is_locked(desktop, session), timeout=5):
        raise Fail("the lock command ran, but the session does not report as locked. "
                   "Lock it yourself before you leave the laptop.")
    print("Locked. Unlock it with your normal password.")


def overlay_running() -> bool:
    state = capture(["systemctl", "--user", "is-active", OVERLAY_UNIT])
    return state in ("active", "activating")


def start_overlay(runner: Runner, desktop: str, session: str, theme: str = THEMES[0]) -> None:
    """Show the overlay; it locks the real session as soon as anyone touches it."""
    screens = connected_screens()
    if screens != 1 or not has_program("tkinter"):
        reason = (f"{screens} screens are connected (the overlay covers exactly one)"
                  if screens != 1 else "tkinter is not installed")
        print(f"Can't use the overlay: {reason}. Using the real lock instead.")
        lock_now(runner, desktop, session)
        return
    if overlay_running():
        print("The overlay is already up.")
        return
    lock = lock_command(runner, desktop, session)
    argv = ["systemd-run", "--user", "--quiet", "--collect", f"--unit={OVERLAY_UNIT}",
            "--description=agent-shield lock overlay",
            # However the overlay ends (tripwire, crash, `agent-shield stop`, even
            # kill -9), systemd then runs the real lock command.
            f"--property=ExecStopPost={shlex.join(lock)}"]
    argv += [f"--setenv={name}={os.environ[name]}" for name in OVERLAY_ENV if name in os.environ]
    argv += [f"--setenv=PYTHONPATH={PACKAGE_ROOT}",
             sys.executable, "-P", "-m", "agent_shield.overlay", f"--theme={theme}",
             f"--desktop={desktop}", f"--session={session or '<session>'}"]
    runner.run(argv)
    print("The overlay is up. Any key, click, mouse movement or focus change locks "
          "your session for real.")


def stop_overlay(runner: Runner) -> None:
    if not overlay_running():
        return
    runner.run(["systemctl", "--user", "stop", OVERLAY_UNIT])
    print("Closed the overlay, which locked your real session (the overlay always ends that way).")
