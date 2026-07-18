from .Tildagon2024 import Tildagon2024Module
from .verbs import PRESS_VERBS, random_verb

from app_components import symbols

# The 2026 frontboard ("Spaceagon", PID 0x2600/0x2601) keeps the six face
# buttons and the mainboard IMU, and adds a 5-way joystick and twelve
# capacitive touch pads (see frontboards/twentysix.py in the firmware). Every
# new input arrives as an ordinary ButtonDown event in group "TwentyTwentySix",
# so it rides the same matching path as the face buttons.
#
# The pads sit in a ring aligned 1:1 with the twelve ring LEDs — the firmware's
# patterndisplay maps TOUCH01->LED0 ... TOUCH12->LED11 — and each fires its own
# event, so a round can ask for one specific pad. The firmware names them
# TOUCH01..TOUCH12; our badge lowercases the event name (see
# Tildagon2024._get_button_name), so they reach us as touch01..touch12. Each
# pad maps to the solar-system body silkscreened on it (BUTTON_COMMANDS below).
#
# The board also exposes two proximity sensors (LEFTPROX/RIGHTPROX); we
# deliberately don't use them.

# The physical silkscreen: each of the twelve pads (touch01..touch12, in ring
# order aligned to ring LEDs 0-11) carries a distinct body.
PAD_PLANETS = [
    ("touch01", "asteroid_belt"),
    ("touch02", "jupiter"),
    ("touch03", "saturn"),
    ("touch04", "uranus"),
    ("touch05", "neptune"),
    ("touch06", "kuiper_belt"),
    ("touch07", "voyager"),
    ("touch08", "sol"),
    ("touch09", "mercury"),
    ("touch10", "venus"),
    ("touch11", "earth"),
    ("touch12", "mars"),
]

# Display label per body command. Rendered as "<verb> <label>".
PLANET_LABELS = {
    "asteroid_belt": "the asteroid belt",
    "jupiter": "Jupiter",
    "saturn": "Saturn",
    "uranus": "Uranus",
    "neptune": "Neptune",
    "kuiper_belt": "the Kuiper belt",
    "voyager": "Voyager",
    "sol": "Sol",
    "mercury": "Mercury",
    "venus": "Venus",
    "earth": "Earth",
    "mars": "Mars",
}

# Body commands, in ring/pad order.
TOUCH_COMMANDS = [planet for _pad, planet in PAD_PLANETS]

TOUCH_VERBS = ("Touch", "Tap", "Press")

JOYSTICK_ARROW_NAMES = {
    "joy_up": "up",
    "joy_down": "down",
    "joy_left": "left",
    "joy_right": "right",
}

JOYSTICK_VERBS = ("Joystick", "Flick the stick", "Stick")

_BUTTON_COMMANDS = {
    "joyup": "joy_up",
    "joydown": "joy_down",
    "joyleft": "joy_left",
    "joyright": "joy_right",
    "joyfire": "fire",
}
# Each pad event (touch01..touch12, lowercased from the firmware's TOUCH01..)
# maps to its body command.
for _pad, _planet in PAD_PLANETS:
    _BUTTON_COMMANDS[_pad] = _planet


class Tildagon2026Module(Tildagon2024Module):
    COMMAND_OPTIONS = [
        "flip", "a", "b", "c", "d", "e", "f", "shake",
        "joy_up", "joy_down", "joy_left", "joy_right", "fire",
    ] + TOUCH_COMMANDS

    BUTTON_GROUP = "TwentyTwentySix"
    FRONTBOARD_PIDS = (0x2600, 0x2601)

    BUTTON_COMMANDS = _BUTTON_COMMANDS

    @classmethod
    def friendly_name(cls):
        return "Tildagon 2026"

    @classmethod
    def decorate(cls, command):
        label = PLANET_LABELS.get(command)
        if label:
            return "{} {}".format(random_verb(TOUCH_VERBS), label)
        arrow_name = JOYSTICK_ARROW_NAMES.get(command)
        if arrow_name:
            return "{} {}".format(
                random_verb(JOYSTICK_VERBS), symbols["arrows"][arrow_name]
            )
        if command == "fire":
            return "{} FIRE".format(random_verb(PRESS_VERBS))
        return Tildagon2024Module.decorate(command)
