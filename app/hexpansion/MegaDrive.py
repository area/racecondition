from .base import HexpansionModule, CommandStatus


class MegaDriveModule(HexpansionModule):
    FRIENDLY_NAME = "MegaDrive"
    COMMAND_OPTIONS = ["start", "a", "up", "down", "left", "right", "b", "c"]

    def __init__(self):
        super().__init__()
        self.last_status = CommandStatus.WAITING

    def on_button_down(self, event):
        button_name = self._get_button_name(event)
        if button_name is None:
            return
        if button_name == self.current_command:
            self.last_status = CommandStatus.PASSED
        elif button_name in self.COMMAND_OPTIONS:
            self.last_status = CommandStatus.FAILED

    def check_command(self):
        return self.last_status

    def _get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        for attr in ("name", "_name", "label"):
            value = getattr(button, attr, None)
            if isinstance(value, str) and value:
                return value.lower()
        return None
