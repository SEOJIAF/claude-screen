# agent-shield

Keep using Claude Code exactly as you do now (type `claude` in your terminal),
and walk away with the lid closed while it works. agent-shield follows Claude
Code by itself:

- **While Claude works on your prompt**, the laptop doesn't go to sleep. If you
  close the lid, the screen **locks** right away instead.
- **When Claude finishes** and the lid is still closed, the laptop goes to sleep,
  as closing the lid normally would.
- **When Claude is idle or not running**, your laptop behaves exactly as before.
- **If a task needs a GUI**, Claude gets its own **private, invisible desktop**
  (a headless Sway session, 1920×1080). It starts the first time Claude uses
  it and never shows up on your screen.
- **Meta+Shift+P** covers your screen with a screensaver (the Gentoo logo
  bouncing into corners), so you can leave the lid open while nobody sees
  what's on it.

## Setup (once)

```sh
sudo dnf install --setopt=install_weak_deps=False sway grim wtype   # Fedora; `agent-shield doctor` prints the Arch/Debian command
./install.sh            # puts `agent-shield` on your PATH (~/.local/bin), then runs `agent-shield doctor`
agent-shield install    # systemd user units, the Sway config, the Claude Code hooks, the Meta+Shift+P hotkey
```

That's all. `install` writes only to your home directory and needs no sudo.
Claude sessions you start after this pick it up by themselves.
`agent-shield install --dry-run` shows every change before it happens.

## Using it

```sh
claude
```

Give Claude its task. When you leave, close the lid. When you come back, open
the lid and unlock with your password as usual.

### Privacy screen: Meta+Shift+P

To keep the lid open but stop others from seeing your screen, press
**Meta+Shift+P**. A screensaver covers everything:
- **The Gentoo logo** bounces off the edges, DVD-screensaver style, in a new
  color at every bounce.
- **Corner hits** set off a burst of sparks and are counted. They aren't left
  to luck: the logo's size is picked so it reaches a corner every minute or
  two (every 90 s on a 1920×1200 screen), and the first one comes after
  about 15 s.
- **At the bottom:** the time and a live status, **Claude is working**
  (green) or **Claude is waiting for you** (amber), so you can tell from
  across the room when it's done.

`--theme orbit` gives the other look: a padlock in an orbiting ring, with
drifting particles and light waves.

Claude keeps working underneath. To get back, press any key or Meta+Shift+P
again. That switches to your normal lock screen, and you unlock with your
password. This is on purpose: if a key could simply hide the screen, anyone at
your laptop could press it too. See [The overlay](#the-overlay) for the details.

- `agent-shield overlay --preview` shows it without any locking (any key closes it);
  add `--theme orbit` to see the other look.
- `agent-shield install --hotkey Meta+Ctrl+P` picks another key; `--hotkey none` binds none.
- The shortcut also appears in System Settings → Keyboard → Shortcuts, as "agent-shield overlay".

Claude learns how to use its private desktop when a session starts (from the
`SessionStart` hook), so you never paste instructions. For reference, these
are the commands it uses:

```sh
agent-shield shot                                         # screenshot; prints the PNG's path
agent-shield run -- swaymsg exec konsole                  # start an app there
agent-shield run -- wtype -s 200 'echo hello' -k Return   # type (keep -s 200: see below)
agent-shield run -- swaymsg -q seat seat0 cursor set 640 400       # move the mouse...
agent-shield run -- swaymsg -q seat seat0 cursor press button1     # ...and click
agent-shield run -- swaymsg -q seat seat0 cursor release button1
agent-shield run -- swaymsg -t get_tree                   # list the windows
```

- `wtype` creates a new virtual keyboard on every call. Without the short
  pause from `-s 200`, apps drop the first key.
- The private desktop has no real mouse, so Sway would tell apps it has no
  pointer and they would ignore clicks. A tiny stdlib-only helper,
  `agent_shield/pointer.py`, keeps one virtual pointer open for the whole session.
- **No ydotool, on purpose.** ydotool types through the kernel (`/dev/uinput`),
  so its input would reach *your* session, including the lock screen.
- Apps that allow one copy per profile (Firefox) need their own profile there:
  `firefox --no-remote --profile ~/.cache/agent-firefox`.

## How it works

```
 Claude Code (in kitty)
   │ hooks: SessionStart · UserPromptSubmit (busy) · Stop / waiting for you (idle) · SessionEnd
   ▼
 /run/user/UID/agent-shield-claude/<session>   one small file per session: pid + busy/idle
   ▼
 agent-shield-guard.service (guard.py, checks once a second)
   ├─ some session busy   → logind "block" lock on sleep + lid switch
   ├─ lid closes while busy → lock the screen (loginctl / swaylock / hyprlock)
   ├─ work done, lid still closed → suspend
   └─ no session left     → stop the private desktop, exit

 private desktop: agent-shield.service = headless Sway, started on first use
   socket in /run/user/UID/agent-shield/ (mode 700), own D-Bus bus, own Xwayland,
   no monitor, no GPU, no real input
```

Why logind locks? On KDE, Plasma's PowerDevil takes the lid over from logind
and *asks* logind to suspend. While the guard's `sleep` block lock is held,
logind refuses that request: `CanSuspend` changes from `yes` to `challenge`,
so it would need an admin password. The `handle-lid-switch` lock does the same
for desktops where logind handles the lid itself (Sway, Hyprland). Idle is
**not** blocked, so your screen still dims and locks itself after the usual time.

With an external screen connected (logind calls that "docked"), the guard
neither locks nor suspends, because you're probably still using the laptop.

The hooks live in `~/.claude/settings.json`. `install` adds them after any
hooks you already have, and `uninstall` removes exactly those and nothing
else. A hook never makes Claude fail: errors are logged and ignored, and each
hook takes about 0.15 s.

### Why a separate desktop at all?

A locked session **cannot be captured or controlled**, on purpose: the lock
screen hides your windows from screenshots and stops input from reaching them.
With the lid closed there isn't even a screen to show them on. So a GUI task
can't run "behind the lock screen"; it needs a desktop that nothing ever displays.

## Commands

| Command | What it does |
| --- | --- |
| `doctor` | Checks programs, desktop, locker, lid settings, polkit, the Claude hooks, the hotkey and the running session. Prints a fix for each problem. Changes nothing. |
| `install` | Writes the systemd user units and the Sway config, adds the Claude Code hooks, and binds the overlay hotkey. Options: `--hotkey KEYS`, `--vnc`, `--size WxH`, `--logind` (the optional drop-in, sudo). |
| `overlay` | Shows the privacy overlay; if it is already up, locks for real (which closes it). `--preview` only shows it; `--theme gentoo` (default) or `orbit`. |
| `uninstall` | Undoes everything `install` did, including the hooks and (asking first) the logind drop-in. |
| `shot [PATH]` | Full-screen PNG of the private desktop (default folder: `~/.cache/agent-shield/shots/`, mode 700). |
| `run -- CMD` | Runs CMD with the private desktop's environment, without your real display's variables. |
| `env` | Prints an `export` block for `eval "$(agent-shield env)"`. |
| `start` / `stop` | Manual mode, without Claude: see below. |

Every command takes `--dry-run`, which prints each action instead of doing it.
Global options: `--yes`, `--user NAME`, `--desktop gnome|kde|sway|hyprland`.

### Manual mode (other agents)

For an agent that isn't Claude Code:
- `agent-shield start` starts the private desktop, keeps the laptop awake, and
  **locks your screen right away**.
- `start --no-lock` skips the lock.
- `start --overlay` shows the tripwire overlay (below) instead of locking.
- `start --vnc` also starts the VNC server.
- `agent-shield stop` stops everything.

### The overlay

Meta+Shift+P, `agent-shield overlay` and `start --overlay` all show the same
overlay. **It is not a lock**: your desktop underneath stays unlocked (that's
what lets Claude keep working in it). That's why it works as a **tripwire**:

- After 3 seconds (time to let go of the keys), any key, click, mouse movement
  or focus change (Alt+Tab, Meta…) **locks your session for real** before the
  overlay closes.
- It runs as `agent-shield-overlay.service`, whose `ExecStopPost` is the real
  lock command. So every way it can end (the tripwire, the hotkey, a crash,
  `stop`, `kill -9`) ends with a real lock.
- If it can't cover the whole screen or can't get the keyboard focus, it locks
  right away.
- While it's up, it holds an `idle` inhibitor, so the screen doesn't dim or
  auto-lock under it. If your session gets locked some other way, the overlay
  closes itself, so you only ever unlock once.
- With more than one screen connected (it covers only one), the real lock is
  used instead.

What remains: shortcuts that KWin handles itself never reach the overlay as key
presses. The overlay sees the focus change and locks, but an effect like
Overview may draw a frame or two of your windows first. Notifications may also
appear on top of it. When that matters, use Meta+L.

## Remote viewing (optional)

```sh
sudo dnf install wayvnc
agent-shield install --vnc     # 127.0.0.1 only, random password, RSA-AES encryption
agent-shield start --vnc
ssh -N -L 5900:127.0.0.1:5900 you@laptop    # from the other computer, then open localhost:5900
```

The password is in `~/.config/agent-shield/wayvnc.conf` (mode 600); it is never
printed or logged. If anything listens on that port beyond localhost,
`start --vnc` stops VNC and fails.

## A separate Linux user (`--user NAME`)

By default the private desktop runs **as you**, so apps in it can read your
files. For a real boundary, `agent-shield install --user agentuser` creates a
user (sudo: `useradd`, `loginctl enable-linger`) and runs the desktop in that
user's own systemd. You then run the agent itself as that user too:
`sudo -iu agentuser`. Claude Code hooks are only installed for your own account.

## Lid and sleep: the optional logind drop-in

`agent-shield install --logind` (sudo, asks first) writes
`/etc/systemd/logind.conf.d/99-agent-shield.conf`, which sets `HandleLidSwitch`,
`HandleLidSwitchExternalPower` and `HandleLidSwitchDocked` to `ignore`.

You normally **don't need it**: the guard's inhibitor covers the lid while
Claude works. The drop-in is permanent until `uninstall`, so the lid then never
suspends the laptop when logind handles it. It is applied with
`systemctl reload systemd-logind`, never `restart`: restarting logind ends your
graphical session.

A laptop running with its lid shut inside a bag can overheat. Actions root
takes (like shutting down on critical battery) still happen.

## Threat model

**What it protects against**

- Someone at the laptop while Claude works: the screen locks when the lid
  closes, and opening it shows your normal lock screen.
- The agent's GUI apps reaching your real desktop by accident. The private
  desktop has its own Wayland socket in a mode-700 directory, its own D-Bus bus
  and Xwayland. It never gets your `WAYLAND_DISPLAY`, `DISPLAY`, `XAUTHORITY`,
  D-Bus address or `SSH_AUTH_SOCK`.
- The network: VNC listens on localhost only and needs a password plus encryption.

**What it does not protect against**

- **Claude itself**: it runs as you, with your files and credentials, just as
  it does without agent-shield. A process running as your user could still
  connect to your real Wayland socket by its full path. Only `--user` is a boundary.
- Anyone with your password or root, or physical attacks (an unencrypted disk,
  a cold boot).
- If you leave with the lid **open**, the screen locks after your desktop's
  normal idle timeout, not immediately.

## Limitations

- **Lid close hasn't been tested on real hardware yet.** Everything else was
  tested on Fedora 44 with KDE Plasma 6.7.
- When Claude stops to ask for your approval, it counts as "waiting for you":
  the laptop may sleep until you're back.
- Software rendering only (`WLR_RENDERER=pixman`), so WebGL and video are slow.
  No audio.
- `eval "$(agent-shield env)"` changes `XDG_RUNTIME_DIR` for that shell, so
  `systemctl --user` and similar commands there need `XDG_RUNTIME_DIR=/run/user/$(id -u)`.

## Files it touches

| Path | Written by | Removed by |
| --- | --- | --- |
| `~/.local/bin/agent-shield` (symlink) | `install.sh` | `install.sh --uninstall` |
| `~/.claude/settings.json`: 5 hooks only | `install` | `uninstall` |
| `~/.config/agent-shield/sway.conf`, `pointer.py`, `state.json` | `install` | `uninstall` |
| `~/.config/systemd/user/agent-shield{,-guard,-inhibit,-vnc}.service` | `install` | `uninstall` |
| `~/.local/share/applications/net.local.agent-shield-overlay.desktop` + its key in `~/.config/kglobalshortcutsrc` | `install` (KDE) | `uninstall` |
| `~/.config/agent-shield/wayvnc.conf` (600), `wayvnc-rsa.pem` | `install --vnc` | `uninstall` |
| `/etc/systemd/logind.conf.d/99-agent-shield.conf` | `install --logind` (sudo) | `uninstall` (sudo, asks) |
| `/run/user/UID/agent-shield/`, `/run/user/UID/agent-shield-claude/` | while running (tmpfs) | systemd / the guard |
| `~/.cache/agent-shield/shots/` (700) | `shot` | you (kept on uninstall) |

Logs: `journalctl --user -t agent-shield` (agent-shield and the guard),
`journalctl --user -u agent-shield` (Sway). The arguments of `run` are never
logged, because they can be text the agent types.

## Development

```sh
sh tests/test_dry_run.sh     # every command through --dry-run in a throwaway $HOME, plus the hook merge logic
```

```
agent_shield/
  cli.py       commands and argument parsing
  claude.py    the Claude Code hooks and the settings.json merge
  guard.py     keeps the laptop awake / locks on lid close while Claude works
  runner.py    the only code that changes things (dry-run, journal logging)
  system.py    read-only facts: distro, desktop, programs, screens, sessions
  session.py   the private desktop: units, environment, separate user
  lock.py      locking the real session; the overlay's systemd unit
  overlay.py   the tripwire overlay window (tkinter)
  scenes.py    what the overlay shows: the bouncing Gentoo logo, or the orbit
  assets/      gentoo-signet.svg
  hotkey.py    the overlay's global hotkey (KDE: kglobalaccel over D-Bus)
  pointer.py   the virtual mouse helper (stdlib Wayland client)
  lid.py       inhibitor info and the logind drop-in
  vnc.py       wayvnc setup and localhost checks
  doctor.py    `agent-shield doctor`
templates/     systemd units, Sway config, wayvnc config, logind drop-in
install.sh     puts the command on your PATH
```

The Gentoo logo (`agent_shield/assets/gentoo-signet.svg`, from gentoo.org) is
a trademark of the Gentoo Foundation, Inc. It is used here as a screensaver
graphic; see the [Gentoo name and logo usage guidelines](https://www.gentoo.org/inside-gentoo/foundation/name-logo-guidelines.html).
