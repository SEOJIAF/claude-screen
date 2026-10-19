"""Command line: `agent-shield <command>`. Each command is one function below."""

import argparse
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from agent_shield import __version__, claude, doctor, guard, hotkey, lid, lock, session, vnc
from agent_shield.runner import Fail, Runner, log, setup_logging
from agent_shield.session import REAL_SESSION_VARS, Target
from agent_shield.system import (DESKTOPS, detect_distro, graphical_session_id, has_program,
                                 install_hint, real_desktop)


@dataclass
class Context:
    runner: Runner
    target: Target
    desktop: str


def require_real_session() -> None:
    """Commands that act on your real desktop must not run from the agent's environment."""
    if os.environ.get("AGENT_SHIELD_SESSION"):
        raise Fail("run this from your normal terminal, not from inside the agent session's "
                   "environment (AGENT_SHIELD_SESSION is set)")


def user_flag(target: Target) -> str:
    return "" if target.is_self else f" --user {target.name}"


# --- commands ------------------------------------------------------------------

def cmd_doctor(args, ctx: Context) -> int:
    return doctor.run(ctx.desktop, ctx.target)


def cmd_install(args, ctx: Context) -> None:
    require_real_session()
    runner, target = ctx.runner, ctx.target
    width, height = parse_size(args.size)
    missing = [p for p in ("sway", "grim", "wtype", "dbus-run-session") if not has_program(p)]
    if args.vnc:
        missing += [p for p in ("wayvnc", "ssh-keygen") if not has_program(p)]
    if missing:
        print(f"Note: still missing {' '.join(missing)}. Install with: "
              f"{install_hint(missing, detect_distro())}")
    state = session.load_state()
    if not target.is_self:
        target = session.create_user(runner, target, state)
    session.install_files(runner, target, width, height, args.vnc_port)
    session.install_own_units(runner)
    if args.vnc:
        vnc.setup(runner, target, args.vnc_port)
    session.daemon_reload(runner, target)
    if not target.is_self:
        session.daemon_reload(runner, Target.lookup())
    if target.is_self:
        claude.install_hooks(runner)
        if args.hotkey != "none":
            hotkey.install(runner, ctx.desktop, args.hotkey)
    if args.logind:
        lid.install_dropin(runner, state, lid.lid_handlers())
    users = state.setdefault("installed_for", [])
    if target.name not in users:
        users.append(target.name)
    session.save_state(runner, state)
    if target.is_self:
        print("\nDone. Just use `claude` as usual: new Claude sessions pick this up by themselves.")
    else:
        print(f"\nInstalled. Start the agent session with: agent-shield start{user_flag(target)}")


def cmd_start(args, ctx: Context) -> None:
    require_real_session()
    runner, target = ctx.runner, ctx.target
    if not session.installed(target):
        if not runner.dry_run:
            raise Fail(f"not installed yet: run `agent-shield install{user_flag(target)}` first")
        print("[dry-run] (not installed yet; showing the plan anyway)")
    session_id = graphical_session_id()
    if not args.no_lock:
        lock.lock_command(runner, ctx.desktop, session_id)  # fail now if we can't lock later
    session.start(runner, target)
    if args.vnc:
        vnc.start(runner, target)
    values = session.read_env(target) or session.EXAMPLE_VALUES
    print(f"\nThe agent session is running (headless Sway, user {target.name}):")
    print(f"  WAYLAND_DISPLAY={values['WAYLAND_DISPLAY']}")
    print(f"  XDG_RUNTIME_DIR={target.agent_dir}")
    who = "" if target.is_self else f" as {target.name}"
    print(f'Before you launch the agent{who}, run:  eval "$(agent-shield env)"\n')
    if args.no_lock:
        print("Your real session stays unlocked (--no-lock).")
    elif args.overlay:
        lock.start_overlay(runner, ctx.desktop, session_id)
    else:
        lock.lock_now(runner, ctx.desktop, session_id)


def cmd_env(args, ctx: Context) -> None:
    values = session.ensure_running(ctx.runner, ctx.target)
    env = session.agent_env(ctx.target, values)
    lines = [f'# agent-shield session of {ctx.target.name}. Use: eval "$(agent-shield env)"']
    if not ctx.target.is_self:
        lines.append(f"# Only {ctx.target.name} can use it: run the agent as that user "
                     f"(sudo -iu {ctx.target.name}).")
    lines += [f"export {key}={shlex.quote(value)}" for key, value in env.items()]
    lines.append("unset " + " ".join(name for name in REAL_SESSION_VARS if name not in env))
    print("\n".join(lines))


def cmd_run(args, ctx: Context) -> None:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise Fail("nothing to run. Usage: agent-shield run -- <command> [arguments]")
    env = session.agent_env(ctx.target, session.ensure_running(ctx.runner, ctx.target))
    ctx.runner.exec(ctx.target.command(command, env=env, unset=REAL_SESSION_VARS), command[0])


def cmd_shot(args, ctx: Context) -> None:
    runner, target = ctx.runner, ctx.target
    env = session.agent_env(target, session.ensure_running(runner, target))
    if args.path:
        path = Path(args.path).expanduser().absolute()
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        folder = cache / "agent-shield" / "shots"
        runner.mkdir(folder, 0o700)  # screenshots may show private things
        path = folder / time.strftime("shot-%Y%m%d-%H%M%S.png")
    if target.is_self:
        runner.run(target.command(["grim", str(path)], env=env, unset=REAL_SESSION_VARS))
    else:  # the other user can't write into your home, so grim sends the PNG to us
        png = runner.run(target.command(["grim", "-"], env=env), capture=True).stdout
        runner.write(path, png, 0o600)
    print(path)


def cmd_overlay(args, ctx: Context) -> None:
    """Show the overlay, or (if it is up) lock for real, which also closes it."""
    require_real_session()
    if args.preview:
        from agent_shield import overlay  # needs tkinter, so only import it here
        overlay.main(["--demo", f"--theme={args.theme}"])
    elif lock.overlay_running():
        lock.stop_overlay(ctx.runner)
    else:
        lock.start_overlay(ctx.runner, ctx.desktop, graphical_session_id(), args.theme)


def cmd_stop(args, ctx: Context) -> None:
    require_real_session()
    lock.stop_overlay(ctx.runner)
    session.stop(ctx.runner, ctx.target)
    print("Stopped the agent session and removed the sleep inhibitor.")


def cmd_uninstall(args, ctx: Context) -> None:
    require_real_session()
    runner, target, me = ctx.runner, ctx.target, Target.lookup()
    state = session.load_state()
    lock.stop_overlay(runner)
    session.stop(runner, target)
    if target.uid is not None:
        session.remove_files(runner, target)  # only the files install wrote
        session.daemon_reload(runner, target)
        if not target.is_self:
            target.remove_dir_if_empty(runner, target.config_dir)
    if target.is_self:
        claude.remove_hooks(runner)
        hotkey.remove(runner)
    others =[name for name in state.get("installed_for", []) if name != target.name]
    if not others:  # the machine-wide parts go with the last install
        session.remove_own_units(runner)
        session.daemon_reload(runner, me)
        lid.remove_dropin(runner, state)
    if not target.is_self:
        session.remove_user(runner, target, state)
    state["installed_for"] = others
    if others or state.get("logind") or state.get("created_users"):
        session.save_state(runner, state)  # something is left to undo later
    else:
        runner.remove(session.state_file())
        me.remove_dir_if_empty(runner, me.config_dir)
    print(f"Uninstalled agent-shield for {target.name}.")
    print("Screenshots in ~/.cache/agent-shield/shots were kept. "
          "`./install.sh --uninstall` removes the command itself.")


def cmd_hook(args, ctx: Context) -> int:
    return claude.handle_hook(args.event)


def cmd_guard(args, ctx: Context) -> int:
    return guard.run()


# --- argument parsing ------------------------------------------------------------

def parse_size(text: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in text.lower().split("x"))
    except ValueError:
        raise Fail(f"--size must look like 1920x1080, not {text!r}") from None
    if not (320 <= width <= 7680 and 240 <= height <= 4320):
        raise Fail(f"--size {text} is out of range")
    return width, height


def build_parser() -> argparse.ArgumentParser:
    # These options work before or after the command. SUPPRESS keeps a subcommand
    # from resetting a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="print every action instead of doing it")
    common.add_argument("--yes", "-y", action="store_true", default=argparse.SUPPRESS,
                        help="answer yes to questions (except deleting a user)")
    common.add_argument("--user", metavar="NAME", default=argparse.SUPPRESS,
                        help="run the agent session as this separate Linux user")
    common.add_argument("--desktop", choices=DESKTOPS, default=argparse.SUPPRESS,
                        help="your desktop, if detecting it from $XDG_CURRENT_DESKTOP fails")

    parser = argparse.ArgumentParser(
        prog="agent-shield", parents=[common],
        description="Let an AI agent use a private, headless Wayland session while your "
                    "real session is locked and the lid is closed.")
    parser.add_argument("--version", action="version", version=f"agent-shield {__version__}")
    commands = parser.add_subparsers(metavar="<command>")

    def add(name, function, text):
        sub = commands.add_parser(name, parents=[common], help=text, description=text)
        sub.set_defaults(func=function)
        return sub

    add("doctor", cmd_doctor, "check dependencies, desktop, lid settings and screen locker")
    sub = add("install", cmd_install, "set up the user units, the Sway config and the Claude Code hooks")
    sub.add_argument("--size", default="1920x1080", help="virtual screen size (default 1920x1080)")
    sub.add_argument("--vnc", action="store_true", help="also set up wayvnc (localhost, password)")
    sub.add_argument("--vnc-port", type=int, default=vnc.PORT, help=f"VNC port (default {vnc.PORT})")
    sub.add_argument("--logind", action="store_true",
                     help="also install the logind lid drop-in (sudo; asks first)")
    sub.add_argument("--hotkey", default=hotkey.DEFAULT_KEY, metavar="KEYS",
                     help=f"key that shows the overlay (default {hotkey.DEFAULT_KEY}; 'none' for no key)")
    sub = add("overlay", cmd_overlay,
              "show the privacy overlay; if it is up, lock for real (which closes it)")
    sub.add_argument("--preview", action="store_true",
                     help="just look at it: never locks, any key closes it")
    sub.add_argument("--theme", choices=lock.THEMES, default=lock.THEMES[0],
                     help="gentoo: the bouncing Gentoo logo (default); orbit: padlock and particles")
    sub = add("start", cmd_start, "start the headless session, then lock your real session")
    sub.add_argument("--overlay", action="store_true",
                     help="show the animated overlay; it locks for real when touched")
    sub.add_argument("--no-lock", action="store_true", help="leave your real session unlocked")
    sub.add_argument("--vnc", action="store_true", help="also start the VNC server")
    add("env", cmd_env, 'print the session\'s variables, for: eval "$(agent-shield env)"')
    sub = add("run", cmd_run, "run a command inside the agent session")
    sub.add_argument("command", nargs=argparse.REMAINDER, help="-- <command> [arguments]")
    sub = add("shot", cmd_shot, "screenshot the agent session and print the PNG's path")
    sub.add_argument("path", nargs="?", help="where to save it (default ~/.cache/agent-shield/shots/)")
    add("stop", cmd_stop, "stop the headless session and remove the sleep inhibitor")
    add("uninstall", cmd_uninstall, "revert everything install did, including the logind drop-in")
    sub = add("hook", cmd_hook, "used by Claude Code's hooks (you don't run this)")
    sub.add_argument("event", choices=["session-start", "busy", "idle", "end"])
    add("guard", cmd_guard, "used by agent-shield-guard.service (you don't run this)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    if os.geteuid() == 0:
        print("agent-shield: don't run this as root. It uses sudo itself, only for the steps "
              "that need it.", file=sys.stderr)
        return 1
    setup_logging()
    if os.environ.get("AGENT_SHIELD_SESSION"):
        # Inside `eval "$(agent-shield env)"`: let systemctl --user find your real systemd.
        os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    ctx = Context(runner=Runner(getattr(args, "dry_run", False), getattr(args, "yes", False)),
                  target=Target.lookup(getattr(args, "user", None)),
                  desktop=getattr(args, "desktop", None) or real_desktop())
    try:
        return args.func(args, ctx) or 0
    except Fail as error:
        log.error("%s", error)
        print(f"agent-shield: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"agent-shield: this command failed (exit {error.returncode}): "
              f"{shlex.join(map(str, error.cmd))}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
