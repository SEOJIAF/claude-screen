"""The agent's headless Sway session: files, systemd units, environment, users."""

import json
import os
import pwd
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from agent_shield.runner import Fail, Runner, capture, succeeds
from agent_shield.system import TEMPLATES, wait_for

UNIT = "agent-shield.service"                  # the headless Sway session
INHIBIT_UNIT = "agent-shield-inhibit.service"  # keeps the laptop awake (`start`)
VNC_UNIT = "agent-shield-vnc.service"          # optional wayvnc server
GUARD_UNIT = "agent-shield-guard.service"      # follows Claude Code (guard.py)

# Variables that point programs at your real desktop. Agent commands run without them.
REAL_SESSION_VARS = ("WAYLAND_DISPLAY", "WAYLAND_SOCKET", "DISPLAY", "XAUTHORITY",
                     "DBUS_SESSION_BUS_ADDRESS", "SWAYSOCK", "I3SOCK",
                     "HYPRLAND_INSTANCE_SIGNATURE")

# What the Sway config writes to <agent dir>/env once the session is up.
SESSION_VARS = ("WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS")

# Shown by --dry-run when the session isn't running. Sway always picks wayland-1
# in an empty runtime directory.
EXAMPLE_VALUES = {"WAYLAND_DISPLAY": "wayland-1"}


@dataclass
class Target:
    """The Linux user whose agent session we manage: you, or a separate --user."""

    name: str
    uid: int | None  # None: the user doesn't exist yet
    home: Path
    is_self: bool

    @classmethod
    def lookup(cls, name: str | None = None) -> "Target":
        me = pwd.getpwuid(os.getuid())
        if name is None or name == me.pw_name:
            return cls(me.pw_name, me.pw_uid, Path.home(), True)
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            return cls(name, None, Path("/home") / name, False)
        return cls(entry.pw_name, entry.pw_uid, Path(entry.pw_dir), False)

    @property
    def config_home(self) -> Path:
        xdg = os.environ.get("XDG_CONFIG_HOME") if self.is_self else None
        return Path(xdg) if xdg else self.home / ".config"

    @property
    def config_dir(self) -> Path:
        return self.config_home / "agent-shield"

    @property
    def unit_dir(self) -> Path:
        return self.config_home / "systemd" / "user"

    @property
    def runtime_dir(self) -> Path:
        return Path(f"/run/user/{self.uid if self.uid is not None else '<uid>'}")

    @property
    def agent_dir(self) -> Path:
        """The agent session's private XDG_RUNTIME_DIR (created by systemd, mode 0700)."""
        return self.runtime_dir / "agent-shield"

    def command(self, argv: list[str], env: dict[str, str] | None = None,
                unset: tuple[str, ...] = ()) -> list[str]:
        """argv as a command that runs as this user, with environment changes.

        For another user this becomes `sudo -u NAME env ...`, and systemctl --user
        gets that user's XDG_RUNTIME_DIR so it can find their systemd.
        """
        env = dict(env or {})
        if not self.is_self:
            env.setdefault("XDG_RUNTIME_DIR", str(self.runtime_dir))
        if not env and not unset:
            return list(argv)
        changes = [arg for name in unset for arg in ("-u", name)]
        changes += [f"{key}={value}" for key, value in env.items()]
        if self.is_self:
            return ["env", *changes, *argv]
        # -C /: your current directory may not be readable by the other user.
        return ["sudo", "-u", self.name, "env", "-C", "/", *changes, *argv]

    def read(self, path: Path) -> str | None:
        if self.is_self:
            try:
                return path.read_text()
            except OSError:
                return None
        out = capture(self.command(["cat", str(path)]))
        return out or None

    def exists(self, path: Path) -> bool:
        return path.exists() if self.is_self else succeeds(self.command(["test", "-e", str(path)]))

    def write(self, runner: Runner, path: Path, content: str, mode: int = 0o644) -> None:
        if self.is_self:
            runner.write(path, content, mode)
            return
        # Run as the other user so they own the file; umask 077 keeps it private until chmod.
        script = 'umask 077 && mkdir -p -- "${1%/*}" && cat > "$1" && chmod "$2" -- "$1"'
        runner.run(self.command(["sh", "-c", script, "sh", str(path), f"{mode:o}"]), input=content)

    def remove(self, runner: Runner, path: Path) -> None:
        if self.is_self:
            runner.remove(path)
        elif self.exists(path):
            runner.run(self.command(["rm", "-f", "--", str(path)]))

    def remove_dir_if_empty(self, runner: Runner, path: Path) -> None:
        if not self.is_self:
            runner.run(self.command(["rmdir", "--ignore-fail-on-non-empty", str(path)]), check=False)
        elif path.is_dir() and not any(path.iterdir()):
            runner.remove(path)


def render(template: str, **values) -> str:
    """Fill the @NAME@ placeholders of templates/<template>."""
    text = (TEMPLATES / template).read_text()
    for key, value in values.items():
        text = text.replace(f"@{key.upper()}@", str(value))
    leftover = re.findall(r"@[A-Z_]+@", text)
    if leftover:
        raise ValueError(f"{template}: no value given for {', '.join(leftover)}")
    return text


def program_path(name: str) -> str:
    """Absolute path of a program for a unit file (systemd wants full paths)."""
    return shutil.which(name) or f"/usr/bin/{name}"


# --- install / uninstall -------------------------------------------------------

def target_files(target: Target) -> list[Path]:
    """Every file `install` may create for this user."""
    return [target.config_dir / "sway.conf",
            target.config_dir / "pointer.py",
            target.config_dir / "wayvnc.conf",
            target.config_dir / "wayvnc-rsa.pem",
            target.config_dir / "wayvnc-rsa.pem.pub",
            target.unit_dir / UNIT,
            target.unit_dir / VNC_UNIT]


def install_files(runner: Runner, target: Target, width: int, height: int,
                  vnc_port: int) -> None:
    """Write the Sway config and the systemd user units from the templates."""
    sway_config = target.config_dir / "sway.conf"
    pointer = target.config_dir / "pointer.py"
    target.write(runner, pointer, (Path(__file__).parent / "pointer.py").read_text())
    target.write(runner, sway_config, render("sway.conf", width=width, height=height,
                                             python=program_path("python3"), pointer=pointer))
    target.write(runner, target.unit_dir / UNIT, render(
        "agent-shield.service",
        dbus_run_session=program_path("dbus-run-session"),
        sway=program_path("sway"),
        sway_config=sway_config))
    target.write(runner, target.unit_dir / VNC_UNIT, render(
        "agent-shield-vnc.service",
        wayvnc=program_path("wayvnc"),
        vnc_config=target.config_dir / "wayvnc.conf",
        vnc_port=vnc_port))


def install_own_units(runner: Runner) -> None:
    """The units that always run as you: the sleep inhibitor (see start()) and the guard."""
    me = Target.lookup()
    runner.write(me.unit_dir / INHIBIT_UNIT, render(
        "agent-shield-inhibit.service",
        systemd_inhibit=program_path("systemd-inhibit"),
        sleep=program_path("sleep")))
    runner.write(me.unit_dir / GUARD_UNIT, render(
        "agent-shield-guard.service",
        agent_shield=Path(__file__).resolve().parent.parent / "bin" / "agent-shield"))


def remove_own_units(runner: Runner) -> None:
    me = Target.lookup()
    runner.run(["systemctl", "--user", "stop", GUARD_UNIT], check=False)
    runner.remove(me.unit_dir / INHIBIT_UNIT)
    runner.remove(me.unit_dir / GUARD_UNIT)


def remove_files(runner: Runner, target: Target) -> None:
    for path in target_files(target):
        target.remove(runner, path)


def installed(target: Target) -> bool:
    return target.exists(target.unit_dir / UNIT) and target.exists(target.config_dir / "sway.conf")


def daemon_reload(runner: Runner, target: Target) -> None:
    runner.run(target.command(["systemctl", "--user", "daemon-reload"]))


# --- start / stop ----------------------------------------------------------------

def is_active(target: Target, unit: str) -> bool:
    return capture(target.command(["systemctl", "--user", "is-active", unit])) == "active"


def journal_hint(target: Target) -> str:
    if target.is_self:
        return "journalctl --user -u agent-shield -n 30"
    return f"sudo journalctl _UID={target.uid} -n 30"


def ensure_running(runner: Runner, target: Target, timeout: float = 15) -> dict[str, str]:
    """Start the private desktop if it isn't running, and return its variables.

    This only starts Sway: it doesn't lock anything or keep the laptop awake.
    """
    values = read_env(target)
    if values:
        return values
    if not runner.dry_run and not installed(target):
        raise Fail("agent-shield is not installed yet: run `agent-shield install` first")
    print("Starting the agent's private desktop...", file=sys.stderr)
    runner.run(target.command(["systemctl", "--user", "start", UNIT]))
    if runner.dry_run:
        return EXAMPLE_VALUES
    if not wait_for(lambda: read_env(target), timeout):
        raise Fail(f"the agent session did not come up within {timeout:.0f}s. "
                   f"Its log: {journal_hint(target)}")
    return read_env(target)


def start(runner: Runner, target: Target) -> None:
    """Start the private desktop, then the sleep inhibitor."""
    ensure_running(runner, target)
    # Always in *your* systemd: logind only lets the user of an active session
    # block the lid switch, and a separate agent user has no session.
    runner.run(["systemctl", "--user", "start", INHIBIT_UNIT])


def stop(runner: Runner, target: Target) -> None:
    runner.run(target.command(["systemctl", "--user", "stop", VNC_UNIT, UNIT]), check=False)
    runner.run(["systemctl", "--user", "stop", INHIBIT_UNIT], check=False)


def read_env(target: Target) -> dict[str, str] | None:
    """How to reach the running session (from <agent dir>/env), or None if it isn't up."""
    text = target.read(target.agent_dir / "env")
    if not text:
        return None
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in SESSION_VARS:  # ignore anything else in the file
            values[key] = value
    return values if "WAYLAND_DISPLAY" in values else None


def agent_env(target: Target, values: dict[str, str]) -> dict[str, str]:
    """Everything a program needs in its environment to use the agent session."""
    return {
        "XDG_RUNTIME_DIR": str(target.agent_dir),
        **values,
        "SWAYSOCK": str(target.agent_dir / "sway.sock"),
        "XDG_CURRENT_DESKTOP": "sway",
        "XDG_SESSION_TYPE": "wayland",
        "AGENT_SHIELD_SESSION": "1",
    }


# --- the optional separate agent user ----------------------------------------

def create_user(runner: Runner, target: Target, state: dict) -> Target:
    """Create the agent user if needed and start its systemd without a login."""
    print(f"Setting up the separate agent user '{target.name}' needs sudo for:")
    if target.uid is None:
        print(f"  sudo useradd --create-home {target.name}")
    print(f"  sudo loginctl enable-linger {target.name}   (runs its systemd without a login)")
    print(f"  sudo -u {target.name} ...                  (writes its config files)")
    if not runner.confirm("Continue?"):
        raise Fail("the agent user was not set up")
    if target.uid is None:
        runner.sudo(["useradd", "--create-home", "--comment", "agent-shield agent session",
                     target.name])
        state.setdefault("created_users", []).append(target.name)
        if not runner.dry_run:
            target = Target.lookup(target.name)
    runner.sudo(["loginctl", "enable-linger", target.name])
    user_manager = ["systemctl", "is-active", "--quiet", f"user@{target.uid}.service"]
    if not runner.dry_run and not wait_for(lambda: succeeds(user_manager), 10):
        raise Fail(f"systemd for {target.name} did not start (check: loginctl user-status {target.name})")
    return target


def remove_user(runner: Runner, target: Target, state: dict) -> None:
    if target.uid is None:
        return
    runner.sudo(["loginctl", "disable-linger", target.name])
    if target.name not in state.get("created_users", []):
        return  # it existed before agent-shield: leave it alone
    # Deleting a user and their home can't be undone, so --yes does not answer this.
    if runner.confirm(f"Delete user {target.name} and {target.home}? This cannot be undone.",
                      allow_yes=False):
        runner.sudo(["loginctl", "terminate-user", target.name], check=False)
        runner.sudo(["userdel", "--remove", target.name])
        state["created_users"].remove(target.name)
    else:
        print(f"Kept user {target.name}. Delete it later with: sudo userdel --remove {target.name}")


# --- state: what install changed, so uninstall can undo it ------------------

def state_file() -> Path:
    return Target.lookup().config_dir / "state.json"


def load_state() -> dict:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return {}


def save_state(runner: Runner, state: dict) -> None:
    runner.write(state_file(), json.dumps(state, indent=2) + "\n", 0o600)
