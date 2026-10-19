"""Optional VNC view of the agent session: wayvnc on 127.0.0.1, with a password.

Other machines reach it through an SSH tunnel; it never listens on the network.
"""

import secrets

from agent_shield.runner import Fail, Runner, capture
from agent_shield.session import VNC_UNIT, Target, render
from agent_shield.system import wait_for

PORT = 5900
LOCAL_ADDRESSES = {"127.0.0.1", "[::1]"}


def config_path(target: Target):
    return target.config_dir / "wayvnc.conf"


def setup(runner: Runner, target: Target, port: int) -> None:
    """Create the wayvnc config with a fresh random password and an encryption key."""
    config = config_path(target)
    if target.exists(config):
        print(f"VNC is already set up ({config}); keeping its password.")
        return
    key = target.config_dir / "wayvnc-rsa.pem"
    # RSA-AES: wayvnc encrypts the connection and only then checks the password.
    runner.run(target.command(["ssh-keygen", "-q", "-m", "pem", "-t", "rsa", "-b", "3072",
                               "-N", "", "-f", str(key)]))
    password = secrets.token_urlsafe(18)
    target.write(runner, config, render("wayvnc.conf", vnc_port=port, vnc_password=password,
                                        rsa_key=key), mode=0o600)
    print(f"VNC password saved in {config} (readable only by {target.name}; not printed or logged).")


def port_of(target: Target) -> int:
    for line in (target.read(config_path(target)) or "").splitlines():
        if line.startswith("port="):
            return int(line.removeprefix("port="))
    return PORT


def listeners(port: int) -> list[str]:
    """The local addresses something listens on for TCP `port`, e.g. ["127.0.0.1"]."""
    out = capture(["ss", "-Hltn", f"sport = :{port}"])
    return [line.split()[3].rsplit(":", 1)[0] for line in out.splitlines() if len(line.split()) > 3]


def exposed(port: int) -> list[str]:
    """Addresses other than localhost that listen on `port` (should always be empty)."""
    return [address for address in listeners(port) if address not in LOCAL_ADDRESSES]


def start(runner: Runner, target: Target) -> None:
    if not runner.dry_run and not target.exists(config_path(target)):
        raise Fail("VNC is not set up yet: run `agent-shield install --vnc` first")
    port = port_of(target)
    runner.run(target.command(["systemctl", "--user", "start", VNC_UNIT]))
    if runner.dry_run:
        return
    wait_for(lambda: listeners(port), timeout=5)
    if exposed(port):
        runner.run(target.command(["systemctl", "--user", "stop", VNC_UNIT]))
        raise Fail(f"something listens on port {port} beyond localhost "
                   f"({', '.join(exposed(port))}); stopped VNC")
    if not listeners(port):
        raise Fail("wayvnc did not start. Its log: journalctl --user -u agent-shield-vnc -n 30")
    print(f"VNC: 127.0.0.1:{port}, user 'agent', password in {config_path(target)}\n"
          f"  From another computer: ssh -N -L {port}:127.0.0.1:{port} {target.name}@<this laptop>\n"
          f"  then point a VNC viewer (e.g. TigerVNC) at localhost:{port}.")
