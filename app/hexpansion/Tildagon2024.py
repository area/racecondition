import math
import time

from .base import HexpansionModule, CommandStatus

import imu

SHAKE_TIMEOUT_MS = 8000


class Tildagon2024Module(HexpansionModule):
    FRIENDLY_NAME = "Tildagon 2024"
    COMMAND_OPTIONS = ["a", "b", "c", "d", "e", "f", "shake"]

    def __init__(self):
        super().__init__()
        self.last_status = CommandStatus.WAITING
        self._shake_started_ms = None
        self._last_accel = None

    def is_connected(self, hexpansions):
        # This module is always available on the badge itself.
        return True

    def generate_command(self):
        command = super().generate_command()
        if command == "shake":
            self._shake_started_ms = time.ticks_ms()
            self._last_accel = self._read_accel_xyz()
        else:
            self._shake_started_ms = None
            self._last_accel = None
        return command

    def on_button_down(self, event):
        button_name = self._get_button_name(event)
        if button_name is None:
            return
        if self.current_command == "shake":
            return
        if button_name == self.current_command:
            self.last_status = CommandStatus.PASSED
        elif button_name in self.COMMAND_OPTIONS:
            self.last_status = CommandStatus.FAILED

    def check_command(self):
        if self.current_command == "shake":
            return self._check_shake()
        return self.last_status

    def _check_shake(self):
        print("[Tildagon] Checking shake command...")
        if self._shake_started_ms is None:
            return CommandStatus.WAITING
        if time.ticks_diff(time.ticks_ms(), self._shake_started_ms) > SHAKE_TIMEOUT_MS:
            print("[Tildagon] Shake command FAILED - timeout")
            return CommandStatus.FAILED
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

    def _get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        for attr in ("name", "_name", "label"):
            value = getattr(button, attr, None)
            if isinstance(value, str) and value:
                return value.lower()
        return None

    def _read_accel_xyz(self):
        print("[Tildagon] Reading accelerometer...")
        return imu.acc_read()