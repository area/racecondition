from .base import HexpansionModule, CommandStatus


SIX_BUTTON_ONLY = {"x", "y", "z", "mode"}

THREE_BUTTON_COMMANDS = ["start", "a", "up", "down", "left", "right", "b", "c"]
SIX_BUTTON_COMMANDS = THREE_BUTTON_COMMANDS + ["x", "y", "z", "mode"]


class MegaDriveModule(HexpansionModule):
    FRIENDLY_NAME = "MegaDrive"
    COMMAND_OPTIONS = THREE_BUTTON_COMMANDS

    def __init__(self):
        super().__init__()
        self.is_six_button = False

    def reset(self):
        super().reset()
        self.is_six_button = False
        self.COMMAND_OPTIONS = THREE_BUTTON_COMMANDS

    def on_button_down(self, event):
        button_name = self._get_button_name(event)
        if button_name is None:
            return
        if button_name in SIX_BUTTON_ONLY and not self.is_six_button:
            self.is_six_button = True
            self.COMMAND_OPTIONS = SIX_BUTTON_COMMANDS
        if button_name == self.current_command:
            self.last_status = CommandStatus.PASSED

    def check_command(self) -> str:
        return self.last_status

    def _get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        if button.group != "SegaController":
            return None

        value = button.name
        return value.lower()