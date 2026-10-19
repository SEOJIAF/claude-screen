#!/bin/sh
# Tests that are safe to run on any machine: every agent-shield command runs
# with --dry-run in a throwaway $HOME. We check the printed plan, and that
# nothing was written. Nothing here needs sudo or a running agent session.
#
#   sh tests/test_dry_run.sh
set -u

repo=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME="$tmp/home"
unset XDG_CONFIG_HOME XDG_CACHE_HOME AGENT_SHIELD_SESSION
mkdir -p "$HOME"

passed=0
failed=0

pass() { passed=$((passed + 1)); echo "ok   $1"; }
fail() { failed=$((failed + 1)); echo "FAIL $1"; printf '%s\n' "$2" | sed 's/^/     | /'; }

shield() { "$repo/bin/agent-shield" "$@"; }

# expect NAME TEXT COMMAND...: COMMAND exits 0 and prints TEXT.
expect() {
    name=$1 text=$2
    shift 2
    if out=$("$@" 2>&1) && printf '%s\n' "$out" | grep -qF -- "$text"; then
        pass "$name"
    else
        fail "$name (wanted: $text)" "$out"
    fi
}

# reject NAME TEXT COMMAND...: COMMAND exits non-zero and prints TEXT.
reject() {
    name=$1 text=$2
    shift 2
    if out=$("$@" 2>&1); then
        fail "$name (should have failed)" "$out"
    elif printf '%s\n' "$out" | grep -qF -- "$text"; then
        pass "$name"
    else
        fail "$name (wanted: $text)" "$out"
    fi
}

# lacks NAME TEXT COMMAND...: COMMAND exits 0 and does NOT print TEXT.
lacks() {
    name=$1 text=$2
    shift 2
    if out=$("$@" 2>&1) && ! printf '%s\n' "$out" | grep -qF -- "$text"; then
        pass "$name"
    else
        fail "$name (must not print: $text)" "$out"
    fi
}

echo "== detection"
detect() { PYTHONPATH="$repo" python3 -P -c "from agent_shield.system import *; $1"; }
expect "KDE from XDG_CURRENT_DESKTOP" "kde" detect 'print(detect_desktop("KDE"))'
expect "GNOME on Ubuntu" "gnome" detect 'print(detect_desktop("ubuntu:GNOME"))'
expect "Hyprland" "hyprland" detect 'print(detect_desktop("Hyprland"))'
expect "unknown desktop" "unknown" detect 'print(detect_desktop("XFCE"))'
expect "Ubuntu is Debian-like" "debian" detect 'print(detect_distro({"ID": "ubuntu", "ID_LIKE": "debian"}))'
expect "Manjaro is Arch-like" "arch" detect 'print(detect_distro({"ID": "manjaro", "ID_LIKE": "arch"}))'
expect "Fedora package hint" "sudo dnf install grim sway" detect 'print(install_hint(["sway", "grim"], "fedora"))'
expect "Debian package hint" "sudo apt install python3-tk" detect 'print(install_hint(["tkinter"], "debian"))'

echo "== install"
expect "writes the Sway config" "write: $HOME/.config/agent-shield/sway.conf" shield install --dry-run --yes
expect "writes the session unit" "write: $HOME/.config/systemd/user/agent-shield.service" shield install --dry-run --yes
expect "writes the inhibitor unit" "agent-shield-inhibit.service" shield install --dry-run --yes
expect "reloads systemd" "run: systemctl --user daemon-reload" shield install --dry-run --yes
expect "installs the guard unit" "agent-shield-guard.service" shield install --dry-run
expect "adds the Claude Code hooks" "write: $HOME/.claude/settings.json" shield install --dry-run
lacks "plain install never uses sudo" "sudo" shield install --dry-run --yes
if [ -e /etc/systemd/logind.conf.d/99-agent-shield.conf ]; then
    expect "logind drop-in already there" "already installed" shield install --dry-run --yes --logind
else
    expect "--logind goes through sudo" "run: sudo tee /etc/systemd/logind.conf.d/99-agent-shield.conf" shield install --dry-run --yes --logind
    expect "logind is reloaded, never restarted" "run: sudo systemctl reload systemd-logind" shield install --dry-run --yes --logind
    lacks "logind is never restarted" "restart" shield install --dry-run --yes --logind
fi
expect "--vnc makes an encryption key" "ssh-keygen" shield install --dry-run --vnc
expect "--vnc config is private" "wayvnc.conf (mode 600)" shield install --dry-run --vnc
reject "bad --size" "--size must look like" shield install --dry-run --size big

echo "== start / stop"
if [ -e "/run/user/$(id -u)/agent-shield/env" ]; then
    echo "skip start launches Sway (the private desktop is running)"
else
    expect "start launches Sway" "run: systemctl --user start agent-shield.service" shield start --dry-run --desktop kde
fi
expect "start holds the inhibitor" "run: systemctl --user start agent-shield-inhibit.service" shield start --dry-run --desktop kde
expect "start locks via logind on KDE" "lock-session" shield start --dry-run --desktop kde
expect "start prints WAYLAND_DISPLAY" "WAYLAND_DISPLAY=wayland-1" shield start --dry-run --desktop kde
expect "start prints XDG_RUNTIME_DIR" "XDG_RUNTIME_DIR=/run/user/$(id -u)/agent-shield" shield start --dry-run --desktop kde
expect "start uses swaylock on Sway" "swaylock --daemonize" shield start --dry-run --desktop sway
expect "start uses hyprlock on Hyprland" "dispatch exec hyprlock" shield start --dry-run --desktop hyprland
lacks "--no-lock does not lock" "lock-session" shield start --dry-run --desktop kde --no-lock
out=$(shield start --dry-run --desktop kde --overlay 2>&1)
if printf '%s\n' "$out" | grep -qF "ExecStopPost=" || printf '%s\n' "$out" | grep -qF "Using the real lock instead"; then
    pass "--overlay locks when it ends (or falls back to the real lock)"
else
    fail "--overlay plan" "$out"
fi
expect "stop stops the session" "run: systemctl --user stop agent-shield-vnc.service agent-shield.service" shield stop --dry-run
expect "stop removes the inhibitor" "run: systemctl --user stop agent-shield-inhibit.service" shield stop --dry-run

echo "== env / run / shot"
if shield env >/dev/null 2>&1; then
    echo "skip env-not-running test (an agent session is running)"
else
    reject "env needs agent-shield installed" "not installed yet" shield env
fi
if [ -e "/run/user/$(id -u)/agent-shield/env" ]; then
    echo "skip run starts the desktop on demand (the private desktop is running)"
else
    expect "run starts the desktop on demand" "run: systemctl --user start agent-shield.service" shield run --dry-run -- wtype hello
fi
expect "run removes the real display" "env -u WAYLAND_DISPLAY" shield run --dry-run -- wtype hello
expect "run sets the agent display" "XDG_RUNTIME_DIR=/run/user/$(id -u)/agent-shield" shield run --dry-run -- wtype hello
expect "run keeps the command" "wtype hello" shield run --dry-run -- wtype hello
reject "run needs a command" "nothing to run" shield run --dry-run
expect "shot uses grim" "grim $tmp/x.png" shield shot --dry-run "$tmp/x.png"

echo "== --user mode"
expect "--user sets up linger with sudo" "run: sudo loginctl enable-linger agent-test-user" shield install --dry-run --user agent-test-user
expect "--user writes files as that user" "sudo -u agent-test-user" shield install --dry-run --user agent-test-user

echo "== overlay and hotkey"
keys() { PYTHONPATH="$repo" python3 -P -c "from agent_shield.hotkey import *; $1"; }
expect "Meta+Shift+P is Qt key 0x12000050" "0x12000050" keys 'print(hex(key_code("Meta+Shift+P")))'
expect "key codes read back" "Meta+Ctrl+F12" keys 'print(key_text(key_code("Meta+Ctrl+F12")))'
reject "unknown modifiers are refused" "unknown modifier" shield install --dry-run --desktop kde --hotkey Hyper+P
expect "KDE: the hotkey goes to Plasma's shortcut service" "setShortcutKeys" shield install --dry-run --desktop kde
expect "KDE: through a .desktop file" "net.local.agent-shield-overlay.desktop" shield install --dry-run --desktop kde
lacks "--hotkey none binds nothing" "kglobalaccel" shield install --dry-run --desktop kde --hotkey none
expect "Sway: prints the bindsym line" "bindsym Mod4+Shift+p exec" shield install --dry-run --desktop sway
out=$(shield overlay --dry-run --desktop kde 2>&1)
if printf '%s\n' "$out" | grep -qF "ExecStopPost=" || printf '%s\n' "$out" | grep -qF "Using the real lock instead"; then
    pass "overlay always ends in a real lock"
else
    fail "overlay plan" "$out"
fi
expect "the overlay uses the Gentoo theme by default" "--theme=gentoo" shield overlay --dry-run --desktop kde
expect "--theme orbit picks the other look" "--theme=orbit" shield overlay --dry-run --desktop kde --theme orbit
scene() { PYTHONPATH="$repo" python3 -P -c "from agent_shield.scenes import *; $1"; }
expect "bouncing goes back and forth" "[0, 5, 10, 5, 0]" scene 'print([bounce(u, 10) for u in (0, 5, 10, 15, 20)])'
expect "tinting keeps white white" "#ffffff" scene 'print(tinted("#fff", 0.3))'
reject "overlay refuses inside the agent environment" "normal terminal" env AGENT_SHIELD_SESSION=1 "$repo/bin/agent-shield" overlay --dry-run

echo "== Claude Code hooks"
hooks() { PYTHONPATH="$repo" python3 -P -c "from agent_shield.claude import *; $1"; }
expect "hooks are added after the user's own" "notify-send" hooks 'print(with_hooks({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send"}]}]}}, "/x/agent-shield")["hooks"]["Stop"][0])'
expect "installing twice changes nothing" "True" hooks 'a = with_hooks({"model": "opus"}, "/x/agent-shield"); print(with_hooks(a, "/x/agent-shield") == a)'
expect "uninstall restores the settings exactly" "True" hooks 's = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}; print(without_hooks(with_hooks(s, "/x/agent-shield")) == s)'
expect "a broken hook input never fails Claude" "exit=0" sh -c 'echo "not json" | "$1" hook end; echo "exit=$?"' sh "$repo/bin/agent-shield"

echo "== safety"
reject "refuses inside the agent environment" "normal terminal" env AGENT_SHIELD_SESSION=1 "$repo/bin/agent-shield" start --dry-run
if grep -rn '0\.0\.0\.0' "$repo/templates" "$repo/agent_shield" >/dev/null; then
    fail "nothing binds to 0.0.0.0" "$(grep -rn '0\.0\.0\.0' "$repo/templates" "$repo/agent_shield")"
else
    pass "nothing binds to 0.0.0.0"
fi
expect "VNC server binds to 127.0.0.1" "127.0.0.1" grep -h '^ExecStart' "$repo/templates/agent-shield-vnc.service"
expect "VNC requires a password" "enable_auth=true" cat "$repo/templates/wayvnc.conf"
lacks "Sway config includes no distro snippets" "include" grep -v '^#' "$repo/templates/sway.conf"
expect "session unit hides the real display" "UnsetEnvironment=WAYLAND_DISPLAY" cat "$repo/templates/agent-shield.service"
left=$(find "$HOME" -mindepth 1 | head -5)
if [ -z "$left" ]; then pass "dry runs wrote nothing"; else fail "dry runs wrote nothing" "$left"; fi

echo "== install.sh"
expect "install.sh has valid syntax" "" sh -n "$repo/install.sh"
expect "install.sh --dry-run plans a symlink" "ln -sfn $repo/bin/agent-shield" sh "$repo/install.sh" --dry-run

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
