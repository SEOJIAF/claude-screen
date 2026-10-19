"""A full-screen "locked" overlay that works as a tripwire.

It only *looks* like a lock screen: your desktop underneath is still unlocked,
and Claude keeps working in it. That is why the first key press, click, mouse
movement or loss of focus locks your real session (loginctl / swaylock /
hyprlock) before the overlay goes away. `agent-shield overlay` runs it as a
systemd unit whose ExecStopPost runs the lock command too, so a crash or
`kill -9` also ends in a real lock. If your session gets locked some other
way, the overlay closes itself, so you only unlock once.

What it shows is a scene from scenes.py (the bouncing Gentoo logo by default).
Preview it without locking anything:  agent-shield overlay --preview
"""

import argparse
import signal
import subprocess
import tkinter as tk

from agent_shield import lock
from agent_shield.claude import live_sessions
from agent_shield.runner import Runner, log, setup_logging
from agent_shield.scenes import DEFAULT_SCENE, FAINT, SCENES

ARM_AFTER_MS = 3000  # time to let go of the keys (or close the lid) before input counts
FRAME_MS = 33        # about 30 frames per second
GREEN, AMBER = "#5ad18f", "#f2b45a"


def claude_status() -> tuple[str, str]:
    """What the status line says, and the color of its dot."""
    sessions = live_sessions()
    if any(session.busy for session in sessions):
        return "Claude is working", GREEN
    if sessions:
        return "Claude is waiting for you", AMBER
    return "Away", FAINT


class Overlay:
    def __init__(self, on_trip, is_locked=None, scene: str = DEFAULT_SCENE,
                 demo: bool = False, seconds: float | None = None):
        self.on_trip = on_trip
        self.is_locked = is_locked or (lambda: False)
        self.scene_class = SCENES[scene]
        self.demo = demo
        self.armed = self.tripped = self.stop_requested = False
        self.frame = 0
        self.scene = None

        root = self.root = tk.Tk(className="agent-shield-overlay")
        root.title("agent-shield")
        root.configure(bg="black", cursor="none")
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        self.canvas = tk.Canvas(root, bg="black", highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        # Draw for the window's real size, and again whenever it changes.
        self.canvas.bind("<Configure>", lambda e: self.new_scene(e.width, e.height))
        self.bind_tripwire()

        # `agent-shield overlay` (again) or `stop` sends SIGTERM; handle it in tick().
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop_requested", True))
        root.after(100, root.focus_force)
        root.after(ARM_AFTER_MS, self.arm)
        if seconds:
            root.after(int(seconds * 1000), lambda: self.trip("time is up", force=True))
        root.after(FRAME_MS, self.tick)

    def new_scene(self, w: int, h: int) -> None:
        if self.scene and (self.scene.w, self.scene.h) == (w, h):
            return
        self.scene = self.scene_class(self.canvas, w, h, self.hint_text())
        self.scene.set_status(*claude_status())

    def hint_text(self) -> str:
        if not self.armed:
            return "Preview" if self.demo else ""
        return "Press any key to close the preview" if self.demo else "Press any key to unlock"

    def tick(self) -> None:
        """Move the scene one frame forward, then schedule the next frame."""
        if self.stop_requested:
            self.trip("asked to stop", force=True)
            return
        if self.scene is not None:
            self.frame += 1
            self.scene.step(self.frame * FRAME_MS / 1000)
            if self.frame % 15 == 0:
                self.scene.update_clock()
            if self.frame % 60 == 0:  # every 2 seconds
                self.scene.set_status(*claude_status())
                if self.is_locked():
                    self.trip("the session got locked", force=True)
                    return
        self.root.after(FRAME_MS, self.tick)

    # --- the tripwire --------------------------------------------------------

    def bind_tripwire(self) -> None:
        for event in ("<KeyPress>", "<ButtonPress>", "<Motion>", "<MouseWheel>"):
            self.root.bind_all(event, lambda e, name=event: self.trip(f"input {name}"))
        # Focus moves away when someone uses a desktop shortcut (Alt+Tab, Meta, ...).
        self.root.bind("<FocusOut>", lambda e: self.root.after(50, self.check_focus))
        self.root.bind("<Unmap>", lambda e: self.trip("the overlay was hidden"))

    def covers_screen(self) -> bool:
        root = self.root
        return (root.winfo_rootx() <= 0 and root.winfo_rooty() <= 0
                and root.winfo_width() >= root.winfo_screenwidth()
                and root.winfo_height() >= root.winfo_screenheight())

    def arm(self) -> None:
        self.armed = True
        if not self.covers_screen():
            # The window manager ignored "full screen": part of the desktop shows.
            if not self.demo:
                self.trip("the overlay could not cover the whole screen")
                return
            print("note: the overlay does not cover the whole screen", flush=True)
        if self.scene:
            self.scene.set_hint(self.hint_text())
        self.check_focus()

    def check_focus(self) -> None:
        if not self.armed or self.tripped:
            return
        if self.root.focus_get() is None:
            self.trip("the overlay does not have the keyboard focus")
        else:
            self.root.after(500, self.check_focus)

    def trip(self, reason: str, force: bool = False) -> None:
        """Lock the real session (unless this is a preview), then close the overlay."""
        if self.tripped or not (self.armed or force):
            return
        self.tripped = True
        log.info("overlay closing: %s", reason)
        if not self.demo and self.scene:
            self.scene.set_hint("Locking…")
            self.root.update_idletasks()
        try:
            self.on_trip()  # the overlay stays on screen until the lock is up
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="agent-shield lock overlay")
    parser.add_argument("--desktop", default="kde")
    parser.add_argument("--session", default="")
    parser.add_argument("--theme", choices=sorted(SCENES), default=DEFAULT_SCENE)
    parser.add_argument("--demo", action="store_true", help="preview only: never lock")
    parser.add_argument("--seconds", type=float, help="close by itself after this many seconds")
    args = parser.parse_args(argv)
    setup_logging()
    if args.demo:
        Overlay(lambda: None, scene=args.theme, demo=True, seconds=args.seconds).run()
        return
    # Keep the screen from dimming and the desktop from locking on idle, so the
    # overlay stays visible. It is a tripwire, so touching anything still locks.
    idle = subprocess.Popen(["systemd-inhibit", "--what=idle", "--mode=block", "--who=agent-shield",
                             "--why=The privacy overlay is up", "sleep", "infinity"])
    try:
        Overlay(on_trip=lambda: lock.lock_now(Runner(), args.desktop, args.session),
                is_locked=lambda: lock.is_locked(args.desktop, args.session),
                scene=args.theme, seconds=args.seconds).run()
    finally:
        idle.terminate()


if __name__ == "__main__":
    main()
