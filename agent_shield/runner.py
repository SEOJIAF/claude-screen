"""Running commands: the one place where agent-shield changes anything.

Every command, file write and removal goes through a Runner, so `--dry-run`
can print the whole plan instead of doing it. Real actions are logged to the
systemd journal (read them with `journalctl -t agent-shield`). Secrets never
go on a command line or into a log message.
"""

import logging
import logging.handlers
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("agent-shield")


class Fail(Exception):
    """A problem we explain to the user, printed without a Python traceback."""


def setup_logging() -> None:
    """Send log messages to the journal through its syslog socket."""
    if log.handlers:
        return  # already set up
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        handler = logging.handlers.SysLogHandler(address="/dev/log")
    except OSError:
        return  # no journal (for example inside a container): don't log
    handler.setFormatter(logging.Formatter("agent-shield[%(process)d]: %(message)s"))
    log.addHandler(handler)


def capture(argv: list[str]) -> str:
    """Run a read-only command and return what it printed ("" if it can't run)."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def succeeds(argv: list[str]) -> bool:
    """Run a read-only command and tell whether it exited with status 0."""
    try:
        return subprocess.run(argv, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Runner:
    """Does (or, with dry_run, only prints) every change agent-shield makes."""

    def __init__(self, dry_run: bool = False, assume_yes: bool = False):
        self.dry_run = dry_run
        self.assume_yes = assume_yes

    def plan(self, what: str) -> None:
        print(f"[dry-run] {what}")

    def run(self, argv, *, input: str | bytes | None = None, capture: bool = False,
            quiet: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        """Run a command that changes something.

        capture=True returns its output as bytes; quiet=True hides what it prints.
        """
        argv = [str(arg) for arg in argv]
        line = shlex.join(argv)
        if self.dry_run:
            self.plan(f"run: {line}")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        log.info("run: %s", line)
        if isinstance(input, str):
            input = input.encode()
        hidden = subprocess.DEVNULL if quiet else None
        return subprocess.run(argv, input=input, check=check,
                              stdout=subprocess.PIPE if capture else hidden, stderr=hidden)

    def sudo(self, argv, **kwargs) -> subprocess.CompletedProcess:
        """Run a command as root. Only used for steps the user agreed to."""
        return self.run(["sudo", *argv], **kwargs)

    def exec(self, argv: list[str], program: str) -> None:
        """Replace this process with argv (used by `agent-shield run`)."""
        if self.dry_run:
            self.plan(f"exec: {shlex.join(argv)}")
            return
        # Log only the program: its arguments may be text the agent types, like a password.
        log.info("exec in agent session: %s", Path(program).name)
        os.execvp(argv[0], argv)

    def write(self, path: Path, content: str | bytes, mode: int = 0o644) -> None:
        """Write a file in your home directory, with exactly the given permissions."""
        if self.dry_run:
            self.plan(f"write: {path} (mode {mode:o})")
            return
        log.info("write: %s", path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode() if isinstance(content, str) else content
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        os.fchmod(fd, mode)  # set before writing, so a secret is never readable by others
        with os.fdopen(fd, "wb") as file:
            file.write(data)

    def mkdir(self, path: Path, mode: int = 0o700) -> None:
        if path.is_dir():
            return
        if self.dry_run:
            self.plan(f"mkdir: {path} (mode {mode:o})")
            return
        log.info("mkdir: %s", path)
        path.mkdir(mode=mode, parents=True)

    def remove(self, path: Path) -> None:
        """Delete a file or directory if it exists."""
        if not path.exists() and not path.is_symlink():
            return
        if self.dry_run:
            self.plan(f"remove: {path}")
            return
        log.info("remove: %s", path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()

    def confirm(self, question: str, allow_yes: bool = True) -> bool:
        """Ask a yes/no question; the default answer is no.

        --yes answers yes, except where allow_yes=False (things that can't be undone).
        """
        if self.dry_run:
            self.plan(f"ask: {question} (showing what 'yes' would do)")
            return True
        if self.assume_yes and allow_yes:
            print(f"{question} [y/N] y (--yes)")
            return True
        if not sys.stdin.isatty():
            print(f"{question} [y/N] n (no terminal to ask on)")
            return False
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
