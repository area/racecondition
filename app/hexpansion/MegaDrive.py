import random
from system.eventbus import eventbus
from events.input import Buttons, BUTTON_TYPES, ButtonDownEvent, ButtonUpEvent

class CommandStatus:
    PASSED = "passed"
    FAILED = "failed"
    WAITING = "waiting"


class MegaDriveModule:
    FRIENDLY_NAME = "MegaDrive"
    COMMAND_OPTIONS = ["start", "a", "up", "down", "left", "right", "b", "c"]

    def __init__(self):
        self.current_command = None
        self.last_status = CommandStatus.WAITING

    def is_connected(self, hexpansions):
        for item in hexpansions.values():
            if item["known"] and item["name"] == self.FRIENDLY_NAME:
                return True
        return False

    def generate_command(self):
        """Generate a new random command and set it as current."""
        self.current_command = random.choice(self.COMMAND_OPTIONS)
        return self.current_command

    def get_supported_commands(self):
        return list(self.COMMAND_OPTIONS)

    def is_supported_command(self, button_name):
        if not button_name:
            return False
        valid = tuple(command.upper() for command in self.COMMAND_OPTIONS)
        return button_name.upper() in valid

    def on_button_down(self, event):
        """App calls this when a button is pressed (app handles routing)."""
        status = self._validate_button(event)
        if status != CommandStatus.WAITING:
            self.last_status = status

    def _validate_button(self, event):
        """Validate a button press against current command."""
        if self.current_command is None:
            return CommandStatus.WAITING

        button_name = self.get_button_name(event)
        if button_name is None:
            return CommandStatus.WAITING

        button_lower = button_name.lower()
        if button_lower == self.current_command:
            return CommandStatus.PASSED
        elif self.is_supported_command(button_name):
            return CommandStatus.FAILED
        else:
            return CommandStatus.WAITING

    def check_command(self, _event=None):
        """Check current command status.

        Returns:
            CommandStatus of last button press (for event handling).
            Resets to WAITING after returning.
        """
        status = self.last_status
        self.last_status = CommandStatus.WAITING
        return status

    def get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None

        for attr in ("name", "_name", "label"):
            value = getattr(button, attr, None)
            if isinstance(value, str) and value:
                return value.upper()

        text = str(button)
        if text:
            return text.upper()

        return None

    def get_button_source(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None

        for attr in ("source", "_source", "app", "_app", "origin", "_origin"):
            value = getattr(button, attr, None)
            if isinstance(value, str) and value:
                return value

        return str(button)

    def is_button(self, event, expected_name):
        name = self.get_button_name(event)
        if name != expected_name.upper():
            return False

        source = self.get_button_source(event)
        if not source:
            return False

        source_upper = source.upper()
        return ("SEGA" in source_upper) or ("MEGADRIVE" in source_upper)