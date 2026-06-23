# Red and green are deliberately excluded: the LED ring uses a red fill for a
# failed/timed-out command and a green fill for a passed one, so a red or green
# badge colour would be indistinguishable from that feedback.
BADGE_COLOURS = {
    "white":  (30, 30, 30),
    "cyan":   ( 0, 30, 30),
    "blue":   ( 0,  0, 40),
    "yellow": (30, 30,  0),
    "purple": (25,  0, 25),
    "orange": (40, 15,  0),
}

# Canonical ordered palette. The server can't import this module (it ships only
# server/), so server/room.py keeps its own copy; tests/test_colour_sync.py
# fails if the two drift apart.
COLOURS = list(BADGE_COLOURS)

CANCEL_HOLD_MS = 2000
