# Red and green are deliberately excluded: the LED ring uses a red fill for a
# failed/timed-out command and a green fill for a passed one, so a red or green
# badge colour would be indistinguishable from that feedback.
#
# These are full-range (0-255) reference colours; the user's "Pattern
# brightness" setting is applied at the hardware write in app.py, matching the
# firmware's own pattern-display convention. The per-hue values keep their
# deliberate perceptual balance (blue reads dimmer, so it's pushed to full).
BADGE_COLOURS = {
    "white":  (191, 191, 191),
    "cyan":   (  0, 191, 191),
    "blue":   (  0,   0, 255),
    "yellow": (191, 191,   0),
    "purple": (159,   0, 159),
    "orange": (255,  96,   0),
}

# Canonical ordered palette. The server can't import this module (it ships only
# server/), so server/room.py keeps its own copy; tests/test_colour_sync.py
# fails if the two drift apart.
COLOURS = list(BADGE_COLOURS)

CANCEL_HOLD_MS = 2000
