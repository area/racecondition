from .Tildagon2024 import Tildagon2024Module
from .verbs import PRESS_VERBS, random_verb

from app_components import symbols

# The 2026 frontboard ("Spaceagon", PID 0x2600/0x2601) keeps the six face
# buttons and the mainboard IMU, and adds a 5-way joystick, two proximity
# sensors and twelve touch pads (see frontboards/twentysix.py in the
# firmware). All of the new inputs arrive as ordinary ButtonDown events in
# group "TwentyTwentySix", so they ride the same matching path as the face
# buttons — only the event names differ from the command tokens, hence
# BUTTON_COMMANDS below.

JOYSTICK_ARROW_NAMES = {
    "joy_up": "up",
    "joy_down": "down",
    "joy_left": "left",
    "joy_right": "right",
}

# Read as whole phrases, like the IMU gestures.
GESTURE_PHRASES_2026 = {
    "wave_left": ("Wave: left sensor", "Wave on the left"),
    "wave_right": ("Wave: right sensor", "Wave on the right"),
    "touch": ("Touch a pad", "Tap any touch pad"),
}

JOYSTICK_VERBS = ("Joystick", "Flick the stick", "Stick")

_BUTTON_COMMANDS = {
    "joyup": "joy_up",
    "joydown": "joy_down",
    "joyleft": "joy_left",
    "joyright": "joy_right",
    "joyfire": "fire",
    "leftprox": "wave_left",
    "rightprox": "wave_right",
}
# Any of the twelve pads satisfies "touch" — players can't tell numbered
# pads apart, so we never ask for a specific one.
for _i in range(1, 13):
    _BUTTON_COMMANDS["touch{}".format(_i)] = "touch"


class Tildagon2026Module(Tildagon2024Module):
    COMMAND_OPTIONS = [
        "flip", "a", "b", "c", "d", "e", "f", "shake",
        "joy_up", "joy_down", "joy_left", "joy_right", "fire",
        "wave_left", "wave_right", "touch",
    ]

    BUTTON_GROUP = "TwentyTwentySix"
    FRONTBOARD_PIDS = (0x2600, 0x2601)

    BUTTON_COMMANDS = _BUTTON_COMMANDS

    @classmethod
    def friendly_name(cls):
        return "Tildagon 2026"

    @classmethod
    def decorate(cls, command):
        phrases = GESTURE_PHRASES_2026.get(command)
        if phrases:
            return random_verb(phrases)
        arrow_name = JOYSTICK_ARROW_NAMES.get(command)
        if arrow_name:
            return "{} {}".format(
                random_verb(JOYSTICK_VERBS), symbols["arrows"][arrow_name]
            )
        if command == "fire":
            return "{} FIRE".format(random_verb(PRESS_VERBS))
        return Tildagon2024Module.decorate(command)
