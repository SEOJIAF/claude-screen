"""The guard: keeps the laptop awake while Claude works, and locks it when the lid closes.

Claude Code's hooks (claude.py) start it as agent-shield-guard.service. Once
a second it looks at the registered Claude sessions and at the lid:

- Some session is busy: hold a logind "block" lock on sleep and the lid
  switch, so closing the lid doesn't suspend the laptop. Idle is not blocked:
  your screen still dims and locks itself after the usual time.
- The lid closes while a session is busy: lock the screen right away.
- No session is busy any more, and the lid was closed during the work and is
  still closed: suspend, as closing the lid would normally have done.
- No Claude session is left: stop the private desktop and exit.

With an external screen connected (logind calls that "docked") it neither
locks nor suspends; you are probably still using the laptop.
"""

import subprocess
import time
from pathlib import Path

from agent_shield import lock
from agent_shield.claude import live_sessions
from agent_shield.lid import LOGIND
from agent_shield.runner import Fail, Runner, capture, log, setup_logging
from agent_shield.session import UNIT, VNC_UNIT, Target, is_active
from agent_shield.system import detect_desktop, graphical_session_id, session_property


def lid_closed() -> bool:
    # Read the ACPI file instead of asking logind: this runs every second.
    for state in Path("/proc/acpi/button/lid").glob("*/state"):
        try:
            return "closed" in state.read_text()
        except OSError:
            break
    return capture(["busctl", "get-property", *LOGIND, "LidClosed"]) == "b true"


def docked() -> bool:
    return capture(["busctl", "get-property", *LOGIND, "Docked"]) == "b true"


def take_inhibitor() -> subprocess.Popen:
    return subprocess.Popen(["systemd-inhibit", "--what=sleep:handle-lid-switch", "--mode=block",
                             "--who=agent-shield", "--why=Claude is working", "sleep", "infinity"])


def release(inhibitor: subprocess.Popen) -> None:
    inhibitor.terminate()
    inhibitor.wait(timeout=5)


def lock_screen(runner: Runner) -> None:
    session = graphical_session_id()
    desktop = detect_desktop(session_property(session, "Desktop"))
    try:
        lock.lock_now(runner, desktop, session)
    except Fail as error:
        log.error("could not lock the screen: %s", error)


def run() -> int:
    setup_logging()
    runner = Runner()
    inhibitor = None
    was_closed = lid_closed()
    kept_awake = False  # the lid closed during work and we stopped the suspend
    log.info("guard started")
    while sessions := live_sessions():
        busy = any(session.busy for session in sessions)
        closed = lid_closed()
        if busy and inhibitor is None:
            inhibitor = take_inhibitor()
            log.info("Claude is working: sleep and the lid switch are blocked")
        if busy and closed and not was_closed and not docked():
            log.info("the lid closed while Claude works: locking the screen")
            lock_screen(runner)
            kept_awake = True
        if not closed:
            kept_awake = False
        if not busy and inhibitor is not None:
            release(inhibitor)
            inhibitor = None
            log.info("Claude is done: sleep is allowed again")
            if kept_awake and closed and not docked():
                log.info("the lid is still closed: suspending")
                runner.run(["systemctl", "suspend"], check=False)
            kept_awake = False
        was_closed = closed
        time.sleep(1)
    if inhibitor is not None:
        release(inhibitor)
    if is_active(Target.lookup(), UNIT):
        runner.run(["systemctl", "--user", "stop", VNC_UNIT, UNIT], check=False)
    log.info("no Claude session left: guard exits")
    return 0
