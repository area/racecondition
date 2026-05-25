import random


class CommandStatus:
    PASSED = "passed"
    FAILED = "failed"
    WAITING = "waiting"


class HexpansionModule:
    FRIENDLY_NAME = None
    COMMAND_OPTIONS = []

    def __init__(self):
        self.current_command = None
        self.last_status = CommandStatus.WAITING

    def is_connected(self, hexpansions):
        for item in hexpansions.values():
            if item["known"] and item["name"] == self.FRIENDLY_NAME:
                return True
        return False

    def generate_command(self):
        self.current_command = random.choice(self.COMMAND_OPTIONS)
        self.last_status = CommandStatus.WAITING
        return self.current_command

    def set_command(self, command):
        if command not in self.COMMAND_OPTIONS:
            raise ValueError("Unsupported command '{}' for {}".format(command, self.FRIENDLY_NAME))
        self.current_command = command
        self.last_status = CommandStatus.WAITING
        return self.current_command

    def get_capabilities(self):
        return {
            "module": self.FRIENDLY_NAME,
            "commands": list(self.COMMAND_OPTIONS),
        }

    def on_button_down(self, event):
        pass

    def check_command(self):
        return CommandStatus.WAITING
