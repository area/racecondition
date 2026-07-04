import math
import time

from .base import HexpansionModule, CommandStatus
from .verbs import PRESS_VERBS, random_verb

import imu

from app_components import symbols
from frontboards.utils import detect_frontboard

# Gesture commands read as a whole phrase, not "<verb> shake".
GESTURE_PHRASES = {
    "shake": ("Shake it!", "Give it a shake", "Rattle it"),
    "flip": ("Flip it", "Turn it over", "Flip it upside down"),
}

# The six face buttons sit in a ring; show the arrow that points at each one so
# players orient to the badge layout instead of the tiny hardware letters. We map
# each letter to a named entry in the firmware's symbols["arrows"] table rather
# than hardcoding the glyph, so the codepoints stay correct against EMFCampFont.
# The bare letter is still what's sent to the server (see COMMAND_OPTIONS).
BUTTON_ARROW_NAMES = {
    "a": "up",
    "b": "north_east",
    "c": "south_east",
    "d": "down",
    "e": "south_west",
    "f": "north_west",
}


class Tildagon2024Module(HexpansionModule):
    COMMAND_OPTIONS = ["flip", "a", "b", "c", "d", "e", "f", "shake"]

    # Firmware button group for events from this frontboard, and the PID(s)
    # detect_frontboard() reports for it. Subclassed per board year.
    BUTTON_GROUP = "TwentyTwentyFour"
    FRONTBOARD_PIDS = (0x2400,)

    # Button name -> command token, for inputs whose event name isn't the
    # command itself (the face buttons a-f map 1:1 so need no entry here).
    BUTTON_COMMANDS = {}

    @classmethod
    def friendly_name(cls):
        return "Tildagon 2024"

    @classmethod
    def decorate(cls, command):
        phrases = GESTURE_PHRASES.get(command)
        if phrases:
            return random_verb(phrases)
        arrow_name = BUTTON_ARROW_NAMES.get(command)
        glyph = symbols["arrows"][arrow_name] if arrow_name else command
        return "{} {}".format(random_verb(PRESS_VERBS), glyph)

    def __init__(self):
        self._has_hexpansions = False
        super().__init__()

    def reset(self):
        super().reset()
        self._shake_started_ms = None
        self._last_accel = None
        self._flip_baseline = None

    def is_connected(self, hexpansions):
        self._has_hexpansions = len(hexpansions) > 0
        return detect_frontboard() in self.FRONTBOARD_PIDS

    def _safe_commands(self):
        if self._has_hexpansions:
            return [c for c in self.COMMAND_OPTIONS if c not in ("shake", "flip")]
        return list(self.COMMAND_OPTIONS)

    def get_capabilities(self):
        return {
            "module": self.friendly_name(),
            "commands": self._safe_commands(),
        }

    def set_command(self, command):
        result = super().set_command(command)
        self._setup_command(command)
        return result

    def _setup_command(self, command):
        self._shake_started_ms = None
        self._last_accel = None
        self._flip_baseline = None
        if command == "shake":
            self._shake_started_ms = time.ticks_ms()
            self._last_accel = self._read_accel_xyz()
        elif command == "flip":
            self._flip_baseline = self._read_accel_xyz()

    def on_button_down(self, event):
        button_name = self._get_button_name(event)
        print("[Tildagon] Button down: {}".format(button_name))
        if button_name is None:
            return
        if self.current_command == "shake":
            return
        if self.BUTTON_COMMANDS.get(button_name, button_name) == self.current_command:
            self.last_status = CommandStatus.PASSED

    def check_command(self):
        if self.current_command == "shake":
            return self._check_shake()
        if self.current_command == "flip":
            return self._check_flip()
        return self.last_status

    def _check_shake(self):
        if self._shake_started_ms is None:
            return CommandStatus.WAITING
        accel = self._read_accel_xyz()
        if self._last_accel is None:
            self._last_accel = accel
            return CommandStatus.WAITING
        delta = math.sqrt(
            (accel[0] - self._last_accel[0]) ** 2 +
            (accel[1] - self._last_accel[1]) ** 2 +
            (accel[2] - self._last_accel[2]) ** 2
        )
        if delta > 15:  # empirically determined threshold
            print("[Tildagon] Shake command PASSED - delta {:.2f}".format(delta))
            return CommandStatus.PASSED
        return CommandStatus.WAITING

    def _check_flip(self):
        if self._flip_baseline is None:
            return CommandStatus.WAITING
        bx, by, bz = self._flip_baseline
        ax, ay, az = self._read_accel_xyz()
        dot = ax * bx + ay * by + az * bz
        mag_a = math.sqrt(ax ** 2 + ay ** 2 + az ** 2)
        mag_b = math.sqrt(bx ** 2 + by ** 2 + bz ** 2)
        if mag_a == 0 or mag_b == 0:
            return CommandStatus.WAITING
        if dot / (mag_a * mag_b) < -0.9:
            return CommandStatus.PASSED
        return CommandStatus.WAITING

    def _get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        # Button has to be from us
        if button.group != self.BUTTON_GROUP:
            return None
        value = button.name
        return value.lower()

    def _read_accel_xyz(self):
        return imu.acc_read()