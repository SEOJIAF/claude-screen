"""`agent-shield doctor`: check what agent-shield needs, and say how to fix problems.

Doctor only looks; it never changes anything.
"""

import json
import os
import sys

from agent_shield import claude, hotkey, lid, lock, session, vnc
from agent_shield.runner import capture
from agent_shield.session import GUARD_UNIT, INHIBIT_UNIT, UNIT, VNC_UNIT, Target
from agent_shield.system import (DESKTOP_NAMES, INSTALL_COMMAND, connected_screens,
                                 detect_distro, graphical_session_id, has_program,
                                 install_hint, os_release, session_property)


class Report:
    def __init__(self):
        self.problems = 0

    def line(self, tag: str, message: str, fix: str | None = None) -> None:
        print(f"  {tag:<5} {message}")
        if fix:
            print(f"        fix: {fix}")

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def ok(self, message, fix=None):
        self.line("ok", message, fix)

    def info(self, message, fix=None):
        self.line("info", message, fix)

    def warn(self, message, fix=None):
        self.line("warn", message, fix)

    def fail(self, message, fix=None):
        self.problems += 1
        self.line("FAIL", message, fix)


def run(desktop: str, target: Target) -> int:
    report = Report()
    distro = detect_distro()
    print("agent-shield doctor")
    session_id = graphical_session_id()
    check_system(report, desktop, distro, session_id)
    check_programs(report, desktop, distro)
    check_locking(report, desktop, session_id)
    check_lid(report)
    check_claude(report)
    check_session(report, target)
    check_security(report, target)
    print()
    if report.problems:
        print(f"{report.problems} problem(s) found. Fix the FAIL lines above.")
        return 1
    print("No problems found.")
    return 0


def check_system(report: Report, desktop: str, distro: str, session_id: str) -> None:
    report.section("System")
    v = sys.version_info
    if v >= (3, 11):
        report.ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        report.fail(f"Python {v.major}.{v.minor} is too old", "agent-shield needs Python 3.11+")
    name = os_release().get("PRETTY_NAME", "unknown Linux")
    if distro in INSTALL_COMMAND:
        report.ok(f"{name} (packages: {INSTALL_COMMAND[distro]})")
    else:
        report.warn(f"{name}: not Arch, Debian/Ubuntu or Fedora; package hints may be wrong")
    kind = session_property(session_id, "Type")
    if desktop == "unknown":
        report.warn(f"Desktop not recognised from XDG_CURRENT_DESKTOP="
                    f"{os.environ.get('XDG_CURRENT_DESKTOP', '')!r}",
                    "pass --desktop gnome|kde|sway|hyprland")
    else:
        report.ok(f"Desktop: {DESKTOP_NAMES[desktop]}, logind session {session_id or '?'} ({kind or '?'})")
    if kind and kind != "wayland":
        report.warn(f"Your session is {kind}, not Wayland; agent-shield is built for Wayland desktops")
    manager = capture(["systemctl", "--user", "is-system-running"])
    if manager in ("running", "degraded"):
        report.ok(f"systemd user manager: {manager}")
    else:
        report.fail("Can't reach your systemd user manager", "log in normally (systemctl --user)")


def check_programs(report: Report, desktop: str, distro: str) -> None:
    report.section("Programs")
    required = ["sway", "swaymsg", "grim", "wtype", "dbus-run-session", "systemd-inhibit",
                "systemd-run", "loginctl", "busctl"]
    required += {"sway": ["swaylock"], "hyprland": ["hyprlock", "hyprctl"]}.get(desktop, [])
    optional = {"wayvnc": "for install --vnc",
                "ssh-keygen": "for the VNC encryption key",
                "tkinter": "for start --overlay"}
    missing = [p for p in required if not has_program(p)]
    found = [p for p in required if p not in missing]
    if found:
        report.ok("Found: " + " ".join(found))
    if missing:
        report.fail("Missing: " + " ".join(missing), install_hint(missing, distro))
    missing_optional = [p for p in optional if not has_program(p)]
    for program in optional:
        if program not in missing_optional:
            report.ok(f"Optional: {program} ({optional[program]})")
    if missing_optional:
        needed_for = ", ".join(f"{p} ({optional[p]})" for p in missing_optional)
        report.warn(f"Optional, missing: {needed_for}", install_hint(missing_optional, distro))


def check_locking(report: Report, desktop: str, session_id: str) -> None:
    report.section("Locking your real session")
    if desktop in ("sway", "hyprland"):
        locker = "swaylock" if desktop == "sway" else "hyprlock"
        if has_program(locker):
            report.ok(f"Locker: {locker}")
        else:
            report.fail(f"{locker} is missing", install_hint([locker], detect_distro()))
    elif not session_id:
        report.fail("No graphical logind session found to lock",
                    "run agent-shield from your desktop session")
    else:
        if desktop == "unknown":
            report.warn("Unknown desktop: will try `loginctl lock-session`, then check it worked")
        locked = session_property(session_id, "LockedHint")
        report.ok(f"Locker: loginctl lock-session {session_id} (locked right now: {locked or '?'})")
    screens = connected_screens()
    if not has_program("tkinter"):
        report.info("Overlay unavailable (no tkinter); `start --overlay` falls back to the real lock")
    elif screens == 1:
        report.ok("Overlay available (1 screen connected)")
    else:
        report.info(f"Overlay unavailable with {screens} screens connected (it covers exactly one); "
                    "`start --overlay` falls back to the real lock")
    key = hotkey.current_key()
    if key and not hotkey.is_listening():
        report.fail(f"Overlay hotkey {key} is stored, but Plasma isn't listening for it",
                    "agent-shield install")
    elif key:
        report.ok(f"Overlay hotkey: {key}")
    elif desktop == "kde":
        report.info("No overlay hotkey", f"agent-shield install --hotkey {hotkey.DEFAULT_KEY}")
    if lock.overlay_running():
        report.info("The overlay is up right now")


def check_lid(report: Report) -> None:
    report.section("Lid and sleep")
    settings = lid.lid_settings()
    external = settings["HandleLidSwitchExternalPower"] or "same"
    report.info(f"logind on lid close: {settings['HandleLidSwitch']} "
                f"(on AC power: {external}, docked: {settings['HandleLidSwitchDocked']})")
    handlers = lid.lid_handlers()
    if handlers:
        report.info(f"{', '.join(handlers)} handles the lid instead of logind, so logind's lid "
                    "settings (and the drop-in) don't apply while it runs. The sleep inhibitor "
                    "blocks the suspend it asks for.")
    if lid.dropin_installed():
        report.ok(f"logind drop-in installed: {lid.DROPIN}")
    else:
        report.info("logind drop-in not installed (optional; `agent-shield install` offers it)")
    if lid.can_inhibit():
        report.ok("polkit lets you block sleep, idle and the lid switch")
    else:
        report.fail("polkit does not let you block sleep and the lid switch",
                    "run agent-shield from your own, active desktop session")


def check_claude(report: Report) -> None:
    report.section("Claude Code")
    if claude.hooks_installed():
        report.ok(f"Hooks installed in {claude.settings_path()}")
    else:
        report.warn("agent-shield's hooks are not in Claude Code's settings", "agent-shield install")
    sessions = claude.live_sessions()
    working = sum(s.busy for s in sessions)
    guard_state = capture(["systemctl", "--user", "is-active", GUARD_UNIT]) or "?"
    if sessions:
        report.info(f"{len(sessions)} Claude session(s), {working} working right now; guard: {guard_state}")
    else:
        report.info("No Claude session registered (they register when they start)")


def check_session(report: Report, target: Target) -> None:
    report.section(f"Agent session (user {target.name})")
    if not target.is_self and target.uid is None:
        report.warn(f"User {target.name} does not exist yet", f"agent-shield install --user {target.name}")
        return
    if session.installed(target):
        report.ok(f"Installed: {target.unit_dir / UNIT}, {target.config_dir / 'sway.conf'}")
    else:
        report.warn("Not installed yet", "agent-shield install" + ("" if target.is_self else f" --user {target.name}"))
        return
    if not session.is_active(target, UNIT):
        report.info("Private desktop not running (it starts by itself when Claude uses it)")
        return
    values = session.read_env(target)
    if not values:
        report.fail(f"Sway runs but has not written {target.agent_dir}/env",
                    session.journal_hint(target))
        return
    report.ok(f"Running: WAYLAND_DISPLAY={values['WAYLAND_DISPLAY']} in {target.agent_dir}")
    mode = capture(target.command(["stat", "-c", "%a", str(target.agent_dir)]))
    if mode == "700":
        report.ok(f"{target.agent_dir} is private (mode 700)")
    else:
        report.fail(f"{target.agent_dir} has mode {mode or '?'}, expected 700")
    swaymsg = target.command(["swaymsg", "-t", "get_outputs", "-r"],
                             env=session.agent_env(target, values))
    try:
        outputs = json.loads(capture(swaymsg) or "[]")
    except ValueError:
        outputs = []
    for output in outputs:
        mode = output.get("current_mode", {})
        report.ok(f"Virtual screen {output.get('name')}: {mode.get('width')}x{mode.get('height')}")
    if not outputs:
        report.warn("Could not ask Sway for its screens (swaymsg)")
    if session.is_active(Target.lookup(), INHIBIT_UNIT) and lid.our_inhibitor_held():
        answer = lid.can_suspend()
        if answer == "yes":
            report.warn("Sleep inhibitor is active, but logind still allows suspend (polkit lets "
                        "you override inhibitors), so the desktop could still suspend on lid close")
        else:
            report.ok(f"Sleep inhibitor active; logind blocks suspend (CanSuspend: {answer})")
    else:
        report.fail("The agent session runs without its sleep inhibitor",
                    f"systemctl --user start {INHIBIT_UNIT}")
    if session.is_active(target, VNC_UNIT):
        port = vnc.port_of(target)
        if vnc.exposed(port):
            report.fail(f"VNC port {port} is reachable beyond localhost: {', '.join(vnc.exposed(port))}",
                        f"systemctl --user stop {VNC_UNIT}")
        else:
            report.ok(f"VNC listens on localhost only: {', '.join(vnc.listeners(port)) or '?'}:{port}")


def check_security(report: Report, target: Target) -> None:
    report.section("Security")
    if target.is_self:
        report.warn(f"The agent session runs as you ({target.name}): its apps can read your files, "
                    "SSH keys and browser data. For a real boundary use --user <name>.")
    else:
        report.ok(f"The agent session runs as the separate user {target.name}")
    config = vnc.config_path(target)
    if target.is_self and config.exists():
        mode = config.stat().st_mode & 0o777
        text = config.read_text()
        if mode == 0o600 and "address=127.0.0.1" in text and "enable_auth=true" in text:
            report.ok("VNC config: localhost only, password required, mode 600")
        else:
            report.fail(f"VNC config {config} is not locked down (mode {mode:o})",
                        f"agent-shield uninstall, then agent-shield install --vnc")
    sway_config = target.read(target.config_dir / "sway.conf") or ""
    if any(line.strip().startswith("include") for line in sway_config.splitlines()):
        report.warn("sway.conf includes other files; distro snippets may leak this session's "
                    "variables into your desktop's services")
    if lid.dropin_installed():
        report.info("The logind drop-in stays active when no agent runs; remove it with "
                    "`agent-shield uninstall`")
