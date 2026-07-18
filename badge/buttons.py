from events.input import BUTTON_TYPES

# Every physical input on the 2024/2026 frontboards — the face buttons, and the
# 2026 joystick and fire button — is defined in the firmware with one of these
# logical button types in its ancestry (see frontboards/twentyfour.py and
# twentysix.py). The 2026 touch pads and proximity sensors emit ButtonDown
# events in the same group but carry no such parent, so this is what tells a
# real button press apart from a stray touch.
_PHYSICAL_BUTTON_TYPES = ("UP", "DOWN", "LEFT", "RIGHT", "CONFIRM", "CANCEL")


def is_physical_button(event):
    # Matches on ancestry like is_cancel(), so it survives firmware refactors of
    # the button attribute layout. Used to keep touch pads from readying a
    # player in the lobby, where any physical press toggles ready.
    return any(BUTTON_TYPES[t] in event.button for t in _PHYSICAL_BUTTON_TYPES)


def is_cancel(event):
    # The event carries the physical frontboard button; the logical CANCEL
    # button sits in its ancestry, which Button.__contains__ walks. Matching
    # on ancestry rather than attribute layout survives firmware refactors —
    # the parent -> parents change in tildagonOS commit 8aa7bd8 silently
    # broke the previous implementation, which read `button.parent` and
    # matched on names.
    return BUTTON_TYPES["CANCEL"] in event.button
