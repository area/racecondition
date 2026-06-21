import math
import random
import time

from .base import HexpansionModule, CommandStatus

import imu


class Tildagon2024Module(HexpansionModule):
    FRIENDLY_NAME = "Tildagon 2024"
    COMMAND_OPTIONS = ["flip", "a", "b", "c", "d", "e", "f", "shake"]

    def __init__(self):
        self._has_hexpansions = False
        super().__init__()

    def reset(self):
        super().reset()
        self._shake_started_ms = None
        self._last_accel = None
        self._flip_baseline = None

    def is_connected(self, hexpansions):
        # For now, only 2024 badges exist.
        # Frontboard.year is being added to the firmware, so use that when available.
        return True

    def _safe_commands(self):
        if self._has_hexpansions:
            return [c for c in self.COMMAND_OPTIONS if c not in ("shake", "flip")]
        return list(self.COMMAND_OPTIONS)

    def get_capabilities(self):
        return {
            "module": self.FRIENDLY_NAME,
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
        if button_name == self.current_command:
            self.last_status = CommandStatus.PASSED

    def check_command(self):
        if self.current_command == "shake":
            return self._check_shake()
        if self.current_command == "flip":
            return self._check_flip()
        return self.last_status

    def _check_shake(self):
        print("[Tildagon] Checking shake command...")
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
        print("[Tildagon] Shake delta: {:.2f}".format(delta))
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
        if button.group != "TwentyTwentyFour":
            return None
        value = button.name
        return value.lower()

    def _read_accel_xyz(self):
        print("[Tildagon] Reading accelerometer...")
        return imu.acc_read()