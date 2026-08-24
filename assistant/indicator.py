"""A small always-on-top spider-web icon that sits tilted in a screen corner and
widens/glows for as long as Spiderman is actively listening for your command --
visual confirmation so you don't have to say the wake word multiple times to check
whether it heard you. Built on Tkinter (no extra dependency) with a transparent
background so only the web itself is visible over the desktop.

The enlarged state is driven by actual recording state, not a fixed animation
timer: it grows the instant the wake word fires and only shrinks back once the
recording genuinely ends (silence timeout or a completed command), so it stays big
for exactly as long as listen.py is still capturing audio.

Threading note: Tkinter's mainloop must own whichever thread calls run() -- in
practice that means listen.py puts the *audio* loop on a background thread and
lets this indicator's Tk mainloop run on the main thread instead, when the
indicator is enabled. activate()/deactivate() are thread-safe (just queue an
event); actual widget updates only ever happen inside the Tk thread's own poll loop.
"""

import math
import queue
import time
import tkinter as tk
from typing import Optional

from assistant.logger import get_logger

log = get_logger(__name__)

IDLE_COLOR = "#8888aa"
ACTIVE_COLOR = "#ff3355"
IDLE_RADIUS = 16
ACTIVE_RADIUS = 26      # the old idle size -- now what it grows to while listening
TILT_DEGREES = 25       # "sitting at an angle" rather than a plain symmetric web
SIZE = 72               # window is SIZE x SIZE pixels
STRANDS = 7
RINGS = 3
POLL_MS = 50

CORNER_MARGIN = 24

# How long after creation to keep re-forcing the window topmost/raised. A single
# "-topmost" attribute set at creation can silently fail to actually take visible
# effect on Windows if DWM/explorer.exe hasn't fully finished starting yet (the
# same early-boot race already worked around elsewhere with sleep delays) -- the
# window exists and is topmost as far as Tkinter is concerned, but doesn't actually
# render above the desktop until something else forces Windows to recompute the
# z-order. Observed symptom: the icon staying invisible after a restart until an
# unrelated window (e.g. VS Code) was opened. Reasserting periodically for a while
# is cheap insurance against that instead of a guess at the "right" one-time delay.
TOPMOST_REASSERT_SECONDS = 30
TOPMOST_REASSERT_EVERY_MS = 1000


class WakeIndicator:
    def __init__(self, corner: str = "top-right", margin: int = CORNER_MARGIN):
        self._queue: "queue.Queue[bool]" = queue.Queue()
        self._corner = corner
        self._margin = margin
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._radius = IDLE_RADIUS
        self._color = IDLE_COLOR
        self._is_active = False
        self._topmost_reassert_until = 0.0
        self._last_topmost_reassert = 0.0

    def activate(self) -> None:
        """Thread-safe: call the instant the wake word fires -- grows and stays
        widened until deactivate() is called (i.e. for the whole recording)."""
        self._queue.put(True)

    def deactivate(self) -> None:
        """Thread-safe: call once recording actually ends (silence timeout or a
        completed command) so the web shrinks back to idle."""
        self._queue.put(False)

    def run(self) -> None:
        """Blocking -- creates the window and runs Tkinter's mainloop. Call this
        from whichever thread should own the GUI (see module docstring)."""
        # A brief head start for the desktop/display config to finish settling --
        # matters specifically right after a fresh boot, when the Task Scheduler
        # "at log on" trigger can fire this process before Windows has finished
        # detecting monitors, which can hand winfo_screenwidth/height() a stale
        # or wrong value and place the window off whatever's actually visible.
        time.sleep(3)

        root = tk.Tk()
        root.overrideredirect(True)          # no title bar/border
        root.attributes("-topmost", True)    # always on top

        transparent_key = "#010101"          # arbitrary color used as the transparency mask
        root.configure(bg=transparent_key)
        root.attributes("-transparentcolor", transparent_key)

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x, y = self._position(screen_w, screen_h)
        root.geometry(f"{SIZE}x{SIZE}+{x}+{y}")
        log.info(
            "Indicator window placed at (%d, %d) on a %dx%d screen (corner=%s)",
            x, y, screen_w, screen_h, self._corner,
        )

        canvas = tk.Canvas(root, width=SIZE, height=SIZE, bg=transparent_key, highlightthickness=0)
        canvas.pack()

        self._root = root
        self._canvas = canvas
        self._draw()
        self._topmost_reassert_until = time.monotonic() + TOPMOST_REASSERT_SECONDS
        root.after(POLL_MS, self._poll)

        try:
            root.mainloop()
        except KeyboardInterrupt:
            pass

    def _position(self, screen_w: int, screen_h: int):
        m = self._margin
        positions = {
            "top-right": (screen_w - SIZE - m, m),
            "top-left": (m, m),
            "bottom-right": (screen_w - SIZE - m, screen_h - SIZE - m),
            "bottom-left": (m, screen_h - SIZE - m),
        }
        return positions.get(self._corner, positions["top-right"])

    def _poll(self) -> None:
        # Redraw only on an actual state transition -- calling Canvas.delete("all")
        # + recreate every poll tick (even with unchanged state) makes Tkinter's
        # non-double-buffered canvas visibly flicker/blink against the desktop,
        # which is what was happening before this guard existed.
        new_state: Optional[bool] = None
        try:
            while True:
                new_state = self._queue.get_nowait()  # last event in the queue wins
        except queue.Empty:
            pass

        if new_state is not None and new_state != self._is_active:
            self._is_active = new_state
            self._radius = ACTIVE_RADIUS if new_state else IDLE_RADIUS
            self._color = ACTIVE_COLOR if new_state else IDLE_COLOR
            self._draw()

        now = time.monotonic()
        if now < self._topmost_reassert_until and now - self._last_topmost_reassert >= TOPMOST_REASSERT_EVERY_MS / 1000:
            self._last_topmost_reassert = now
            self._root.attributes("-topmost", True)
            self._root.lift()

        self._root.after(POLL_MS, self._poll)

    def _draw(self) -> None:
        c = self._canvas
        c.delete("all")
        cx, cy = SIZE / 2, SIZE / 2
        r = self._radius
        color = self._color
        angle_offset = math.radians(TILT_DEGREES)

        spokes = []
        for i in range(STRANDS):
            angle = angle_offset + (2 * math.pi * i / STRANDS)
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            spokes.append((x, y))
            c.create_line(cx, cy, x, y, fill=color, width=2)

        for ring in range(1, RINGS + 1):
            ring_r = r * ring / RINGS
            ring_points = []
            for i in range(STRANDS):
                angle = angle_offset + (2 * math.pi * i / STRANDS)
                ring_points.append((cx + ring_r * math.cos(angle), cy + ring_r * math.sin(angle)))
            for i in range(STRANDS):
                x1, y1 = ring_points[i]
                x2, y2 = ring_points[(i + 1) % STRANDS]
                c.create_line(x1, y1, x2, y2, fill=color, width=1)
