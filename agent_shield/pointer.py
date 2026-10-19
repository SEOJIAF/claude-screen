"""Keep a virtual mouse plugged into the agent's Sway session.

The headless session has no real mouse, so Sway tells apps "this seat has no
pointer" and they ignore clicks. This tiny Wayland client creates one virtual
pointer (the wlroots protocol zwlr_virtual_pointer_v1) and keeps it open.
With it, `swaymsg seat seat0 cursor set X Y` and `... cursor press button1`
really click in apps.

It speaks the Wayland wire protocol itself, so it needs only the Python
standard library and no other part of agent-shield: `install` copies it next
to the Sway config (so a separate --user can run it too), and Sway starts it.
"""

import os
import socket
import struct
import sys

# The ids we give our Wayland objects. The display is always object 1.
DISPLAY, REGISTRY, SYNC, SEAT, MANAGER, POINTER = 1, 2, 3, 4, 5, 6


def message(object_id: int, opcode: int, payload: bytes = b"") -> bytes:
    """A Wayland request: object id, then (size << 16 | opcode), then the arguments."""
    return struct.pack("=II", object_id, (8 + len(payload)) << 16 | opcode) + payload


def string(text: str) -> bytes:
    """A Wayland string: its length (with the NUL), the bytes, padding to 4 bytes."""
    data = text.encode() + b"\0"
    return struct.pack("=I", len(data)) + data + b"\0" * (-len(data) % 4)


def read_events(sock: socket.socket):
    """Yield (object id, opcode, arguments) for each event the compositor sends."""
    buffer = b""
    while data := sock.recv(4096):
        buffer += data
        while len(buffer) >= 8:
            object_id, word = struct.unpack("=II", buffer[:8])
            size = word >> 16
            if len(buffer) < size:
                break
            yield object_id, word & 0xFFFF, buffer[8:size]
            buffer = buffer[size:]


def main() -> int:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(os.path.join(os.environ["XDG_RUNTIME_DIR"], os.environ["WAYLAND_DISPLAY"]))

    # Ask for the list of globals; the sync callback fires once it is complete.
    sock.sendall(message(DISPLAY, 1, struct.pack("=I", REGISTRY))   # wl_display.get_registry
                 + message(DISPLAY, 0, struct.pack("=I", SYNC)))    # wl_display.sync
    names = {}
    events = read_events(sock)
    for object_id, opcode, args in events:
        if object_id == REGISTRY and opcode == 0:  # wl_registry.global(name, interface, version)
            name, length = struct.unpack("=II", args[:8])
            names[args[8:8 + length - 1].decode()] = name
        elif object_id == SYNC:  # wl_callback.done
            break

    manager = "zwlr_virtual_pointer_manager_v1"
    if "wl_seat" not in names or manager not in names:
        print(f"agent-shield pointer: the compositor does not offer {manager}", file=sys.stderr)
        return 1

    def bind(interface: str, new_id: int) -> bytes:  # wl_registry.bind(name, interface, version 1, id)
        return message(REGISTRY, 0, struct.pack("=I", names[interface]) + string(interface)
                       + struct.pack("=II", 1, new_id))

    sock.sendall(bind("wl_seat", SEAT) + bind(manager, MANAGER)
                 + message(MANAGER, 0, struct.pack("=II", SEAT, POINTER)))  # create_virtual_pointer
    print("agent-shield pointer: virtual pointer ready", flush=True)

    # Keep the connection, and with it the pointer, open until Sway exits.
    for object_id, opcode, args in events:
        if object_id == DISPLAY and opcode == 0:  # wl_display.error
            print(f"agent-shield pointer: Wayland error {args!r}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
