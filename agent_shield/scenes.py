"""What the overlay shows. A scene draws itself on a Tk canvas, and step() moves it one frame.

- GentooScene ("gentoo", the default): the Gentoo logo bouncing off the edges
  of the screen like the old DVD screensaver. It changes color at every
  bounce, and hitting a corner exactly is celebrated.
- OrbitScene ("orbit"): a padlock in an orbiting ring, drifting particles and
  light waves.

Both show the time and Claude's status (set_status) and a hint (set_hint).
"""

import colorsys
import math
import random
import re
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

BG_TOP, BG_BOTTOM = "#0d1326", "#020309"
TEXT, MUTED, FAINT = "#e8edff", "#8b95b5", "#4d5675"
BLUE, VIOLET, TEAL = "#7aa2ff", "#c08bff", "#5ad1c8"

LOGO = Path(__file__).parent / "assets" / "gentoo-signet.svg"


def mix(color_a: str, color_b: str, t: float) -> str:
    """Blend two "#rrggbb" colors: t=0 gives color_a, t=1 gives color_b."""
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def pick_font(*families: str) -> str:
    available = set(tkfont.families())
    return next((family for family in families if family in available), "TkDefaultFont")


class Scene:
    """What every scene shares: canvas, sizes, font, status line and hint."""

    def __init__(self, canvas: tk.Canvas, w: int, h: int, hint: str):
        self.c, self.w, self.h = canvas, w, h
        self.s = h / 1080  # every size is given for a 1080-pixel-high screen
        self.font = pick_font("Inter", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans")
        self.status = ("", FAINT)
        canvas.delete("all")
        self.draw()
        self.hint = self.text(w / 2, h - 60 * self.s, 15, FAINT, text=hint)
        self.update_clock()

    def text(self, x, y, size, color, **options):
        return self.c.create_text(x, y, fill=color, font=(self.font, -round(size * self.s)), **options)

    def make_status(self, y: float) -> None:
        self.status_y = y
        self.status_text = self.text(self.w / 2, y, 17, MUTED)
        self.status_dot = self.c.create_oval(0, 0, 0, 0, outline="", fill=FAINT)

    def set_status(self, words: str, color: str) -> None:
        """Write "Locked · Claude is working" and put the colored dot in front of it."""
        c, s = self.c, self.s
        self.status = (words, color)
        c.itemconfigure(self.status_text, text=f"Locked  ·  {words}")
        left = c.bbox(self.status_text)[0] - 18 * s
        c.coords(self.status_dot, left - 5 * s, self.status_y - 5 * s,
                 left + 5 * s, self.status_y + 5 * s)

    def set_hint(self, text: str) -> None:
        self.c.itemconfigure(self.hint, text=text)

    def pulse_dot(self, t: float) -> None:
        _, color = self.status
        self.c.itemconfigure(self.status_dot, fill=mix(mix(BG_BOTTOM, color, 0.35), color,
                                                       (math.sin(t * 3) + 1) / 2))

    def draw(self) -> None:
        raise NotImplementedError

    def update_clock(self) -> None:
        raise NotImplementedError

    def step(self, t: float) -> None:
        raise NotImplementedError


# --- the bouncing Gentoo logo ------------------------------------------------------

def bounce(travel: int, span: int) -> int:
    """Where something moving back and forth over 0..span is after `travel` pixels."""
    position = travel % (2 * span)
    return position if position <= span else 2 * span - position


def tinted(svg: str, hue: float) -> str:
    """The SVG with every color's hue turned by `hue` (0..1). White stays white."""
    def turn(match):
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(d * 2 for d in digits)
        r, g, b = (int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        r, g, b = colorsys.hls_to_rgb((h + hue) % 1, l, min(1, s * 1.6))
        return "#" + "".join(f"{round(v * 255):02x}" for v in (r, g, b))
    return re.sub(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", turn, svg)


class GentooScene(Scene):
    COLORS = 8           # how many tints the logo cycles through
    FIRST_CORNER_S = 15  # start so the first corner hit comes after about this long

    def draw(self) -> None:
        c, w, h, s = self.c, self.w, self.h, self.s
        c.create_rectangle(0, 0, w, h, fill="#000000", outline="")

        # Moving diagonally, the logo is in a corner whenever it has travelled a
        # common multiple of the free width and height. Pick the size (about a
        # quarter of the screen height) that makes that distance shortest.
        self.speed = max(1, round(3 * s))
        self.period, self.size = min((math.lcm(w - size, h - size), size)
                                     for size in range(int(220 * s), int(300 * s) + 1))
        self.span_x, self.span_y = w - self.size, h - self.size
        self.travel = (self.period - self.speed * 30 * self.FIRST_CORNER_S) % self.period

        svg = LOGO.read_text()
        scale = f"svg -scale {self.size / 400}"
        self.images = [tk.PhotoImage(data=tinted(svg, i / self.COLORS), format=scale)
                       for i in range(self.COLORS)]
        self.color = 0

        self.clock = self.text(w / 2, h - 150 * s, 40, TEXT)
        self.make_status(h - 105 * s)
        self.corners = 0
        self.counter = self.text(w / 2, h - 30 * s, 13, FAINT, text="")  # not in a corner: those get hit
        self.logo = c.create_image(0, 0, image=self.images[0], anchor="nw")
        self.sparks = []  # [item, x, y, dx, dy, frames left]

    def update_clock(self) -> None:
        self.c.itemconfigure(self.clock, text=time.strftime("%H:%M  ·  %A, %-d %B"))

    def step(self, t: float) -> None:
        c, before = self.c, self.travel
        self.travel += self.speed
        hit_x = before // self.span_x != self.travel // self.span_x
        hit_y = before // self.span_y != self.travel // self.span_y
        x, y = bounce(self.travel, self.span_x), bounce(self.travel, self.span_y)
        c.coords(self.logo, x, y)
        if hit_x or hit_y:
            self.color = (self.color + 1) % self.COLORS
            c.itemconfigure(self.logo, image=self.images[self.color])
        if hit_x and hit_y:
            self.corners += 1
            c.itemconfigure(self.counter, text=f"corners hit: {self.corners}")
            self.celebrate(x, y)
        self.move_sparks()
        self.pulse_dot(t)

    def celebrate(self, x: float, y: float) -> None:
        """Burst of sparks out of the corner the logo just hit."""
        corner_x = 0 if x < self.w / 2 else self.w
        corner_y = 0 if y < self.h / 2 else self.h
        away_x, away_y = (1 if corner_x == 0 else -1), (1 if corner_y == 0 else -1)
        for _ in range(60):
            angle = random.uniform(0.1, math.pi / 2 - 0.1)
            speed = random.uniform(4, 16) * self.s
            r = random.uniform(2, 5) * self.s
            color = random.choice((TEXT, BLUE, VIOLET, TEAL, "#ffd27a"))
            item = self.c.create_oval(corner_x - r, corner_y - r, corner_x + r, corner_y + r,
                                      fill=color, outline="")
            self.sparks.append([item, corner_x, corner_y, away_x * speed * math.cos(angle),
                                away_y * speed * math.sin(angle), random.randint(30, 60)])

    def move_sparks(self) -> None:
        for spark in self.sparks:
            item, x, y, dx, dy, left = spark
            spark[1], spark[2], spark[3], spark[4], spark[5] = x + dx, y + dy, dx * 0.96, dy * 0.96, left - 1
            self.c.move(item, dx, dy)
            if left <= 1:
                self.c.delete(item)
        self.sparks = [spark for spark in self.sparks if spark[5] > 0]


# --- the orbiting ring --------------------------------------------------------------

def rounded_rect(canvas, x1, y1, x2, y2, r, **options):
    """A rectangle with round corners (a smoothed polygon with doubled corner points)."""
    points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
              x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(points, smooth=True, **options)


class OrbitScene(Scene):
    def draw(self) -> None:
        c, w, h, s = self.c, self.w, self.h, self.s

        # Background: a vertical gradient made of thin bands.
        bands = 90
        for i in range(bands):
            c.create_rectangle(0, h * i / bands, w, h * (i + 1) / bands + 1, outline="",
                               fill=mix(BG_TOP, BG_BOTTOM, i / (bands - 1)))

        # Slowly rising, twinkling particles: [item, x, y, radius, speed, phase].
        self.particles = []
        for _ in range(110):
            x, y, r = random.uniform(0, w), random.uniform(0, h), random.uniform(0.8, 2.4) * s
            item = c.create_oval(x - r, y - r, x + r, y + r, outline="", fill=FAINT)
            self.particles.append([item, x, y, r, random.uniform(0.15, 0.6) * s,
                                   random.uniform(0, math.tau)])

        # Three soft light waves drifting along the bottom of the screen.
        self.waves = [c.create_line(0, 0, 0, 0, smooth=True, width=(3 - i) * s,
                                    fill=mix(BG_BOTTOM, color, 0.45 - 0.1 * i))
                      for i, color in enumerate((BLUE, VIOLET, TEAL))]

        # A ring with two orbiting arcs and a soft, breathing glow around a padlock.
        self.cx, self.cy, self.radius = w / 2, h * 0.36, 120 * s
        cx, cy, r = self.cx, self.cy, self.radius
        self.glow = [c.create_oval(0, 0, 0, 0, width=2 * s,
                                   outline=mix(BG_TOP, BLUE, 0.22 - 0.06 * i)) for i in range(3)]
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#1a2340", width=4 * s)
        box = (cx - r, cy - r, cx + r, cy + r)
        self.arc_a = c.create_arc(*box, start=90, extent=80, style="arc", outline=BLUE, width=4 * s)
        self.arc_b = c.create_arc(*box, start=270, extent=50, style="arc", outline=VIOLET, width=4 * s)
        self.draw_padlock(cx, cy + 5 * s, 34 * s)

        self.clock = self.text(cx, h * 0.62, 112, TEXT)
        self.date = self.text(cx, h * 0.62 + 88 * s, 24, MUTED)
        self.make_status(h * 0.62 + 160 * s)

    def draw_padlock(self, x: float, y: float, size: float) -> None:
        c, k = self.c, size
        hole = mix(BG_TOP, BG_BOTTOM, y / self.h)
        # Shackle: half a ring with two straight legs.
        c.create_arc(x - 0.62 * k, y - 1.45 * k, x + 0.62 * k, y - 0.2 * k, start=0, extent=180,
                     style="arc", outline=TEXT, width=0.22 * k)
        for side in (-1, 1):
            c.create_line(x + side * 0.62 * k, y - 0.84 * k, x + side * 0.62 * k, y - 0.1 * k,
                          fill=TEXT, width=0.22 * k)
        # Body and keyhole.
        rounded_rect(c, x - 0.95 * k, y - 0.2 * k, x + 0.95 * k, y + 1.05 * k, 0.24 * k, fill=TEXT)
        c.create_oval(x - 0.17 * k, y + 0.16 * k, x + 0.17 * k, y + 0.5 * k, fill=hole, outline="")
        c.create_rectangle(x - 0.06 * k, y + 0.42 * k, x + 0.06 * k, y + 0.78 * k, fill=hole, outline="")

    def update_clock(self) -> None:
        self.c.itemconfigure(self.clock, text=time.strftime("%H:%M"))
        self.c.itemconfigure(self.date, text=time.strftime("%A, %-d %B"))

    def step(self, t: float) -> None:
        c, s = self.c, self.s
        for p in self.particles:
            item, x, y, r, speed, phase = p
            y -= speed
            if y < -10:  # off the top: start again at the bottom
                y, p[1] = self.h + 10, random.uniform(0, self.w)
            p[2] = y
            dx = math.sin(t * 0.5 + phase) * 8 * s
            c.coords(item, p[1] + dx - r, y - r, p[1] + dx + r, y + r)
            c.itemconfigure(item, fill=mix(FAINT, "#a9c1ff", (math.sin(t * 1.7 + phase) + 1) * 0.4))
        for i, wave in enumerate(self.waves):
            base, height = self.h * (0.84 + 0.035 * i), (26 - 6 * i) * s
            points = []
            for step in range(41):
                x = self.w * step / 40
                y = base + height * math.sin(step / 40 * math.tau * (1.2 + 0.3 * i)
                                             + t * (0.6 + 0.25 * i) + i)
                points += [x, y]
            c.coords(wave, *points)
        c.itemconfigure(self.arc_a, start=(90 - t * 80) % 360)
        c.itemconfigure(self.arc_b, start=(270 + t * 55) % 360)
        for i, glow in enumerate(self.glow):
            r = self.radius + (14 + 12 * i) * s + math.sin(t * 1.4 - i * 0.6) * 5 * s
            c.coords(glow, self.cx - r, self.cy - r, self.cx + r, self.cy + r)
        self.pulse_dot(t)


SCENES = {"gentoo": GentooScene, "orbit": OrbitScene}
DEFAULT_SCENE = "gentoo"
