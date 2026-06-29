import time

# The badge ring is 12 LEDs (tildagonos.leds indices 1..12).
LED_COUNT = 12

# Flash colours, full-range like BADGE_COLOURS; the user's "Pattern brightness"
# setting is applied at the hardware write in app.py.
FLASH_GREEN = (0, 255, 0)
FLASH_RED = (255, 0, 0)

FLASH_DURATION_MS = 850

TOP_LED = 0      # ring index treated as "top" for the fill animation
FILL_HOLD = 0.8  # fraction of the duration spent filling; the rest holds full
FILL_EDGE = 1.0  # soft leading edge, in LEDs


def _blend(base, flash, b):
    # Linear per-channel blend: b=1 is pure flash, b=0 is base.
    return tuple(int(base[k] + (flash[k] - base[k]) * b) for k in range(3))


def _ring_distance(a, b, count):
    # Shortest hop count between two positions around the ring.
    d = abs(a - b) % count
    return min(d, count - d)


def fill_frame(base, flash, progress, count=LED_COUNT, start=TOP_LED, hold=FILL_HOLD):
    """A ring frame where `flash` fills outward from `start` on both sides.

    Two fronts spread from `start`, meeting at the opposite point by
    progress=hold; the ring then holds full `flash` until progress=1 (where the
    caller settles it back to `base`). `start=TOP_LED` fills downward; passing
    the bottom LED fills upward.
    """
    front = min(1.0, progress / hold) * (count / 2)
    frame = []
    for i in range(count):
        depth = _ring_distance(i, start, count)
        if depth <= front:
            b = 1.0
        elif depth <= front + FILL_EDGE:
            b = 1 - (depth - front) / FILL_EDGE
        else:
            b = 0.0
        frame.append(_blend(base, flash, b))
    return frame


def fill_up_frame(base, flash, progress, count=LED_COUNT):
    """Like `fill_frame` but the fronts rise from the bottom to meet at the top."""
    return fill_frame(base, flash, progress, count, start=(TOP_LED + count // 2) % count)


class LedRing:
    """Drives the LED ring: a steady identity colour with transient comet
    animations that run around the ring on pass/fail.

    `write` is a callback taking a list of `count` (r, g, b) tuples; it owns the
    actual hardware access so this module stays testable without a badge.
    """

    def __init__(self, write, count=LED_COUNT):
        self._write = write
        self._count = count
        self._base = (0, 0, 0)
        self._flash_colour = None
        self._flash_start_ms = None
        self._frame_fn = fill_frame

    def set_base(self, rgb):
        self._base = rgb or (0, 0, 0)
        # Don't stomp on a running comet; it settles to the new base when done.
        if self._flash_colour is None:
            self._write([self._base] * self._count)

    def flash(self, colour, now_ms, frame_fn=fill_frame):
        self._flash_colour = colour
        self._flash_start_ms = now_ms
        self._frame_fn = frame_fn

    def update(self, now_ms):
        if self._flash_colour is None:
            return  # steady state — nothing to redraw, leave the bus alone
        elapsed = time.ticks_diff(now_ms, self._flash_start_ms)
        if elapsed >= FLASH_DURATION_MS:
            self._flash_colour = None
            self._flash_start_ms = None
            self._write([self._base] * self._count)
            return
        self._write(self._frame_fn(self._base, self._flash_colour, elapsed / FLASH_DURATION_MS, self._count))
