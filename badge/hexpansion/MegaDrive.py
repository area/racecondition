from .base import HexpansionModule, CommandStatus, random_verb

from app_components import symbols


PRESS_VERBS = ("Press", "Hit", "Push", "Smash", "Bash")


# The D-pad directions show the matching arrow glyph so players orient to the
# pad instead of reading the word. We map each direction to a named entry in the
# firmware's symbols["arrows"] table rather than hardcoding the glyph, so the
# codepoints stay correct against EMFCampFont. The bare direction is still what's
# sent to the server (see THREE_BUTTON_COMMANDS).
BUTTON_ARROW_NAMES = {
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}


SIX_BUTTON_ONLY = {"x", "y", "z", #"mode"
                   }

THREE_BUTTON_COMMANDS = ["start", "up", "down", "left", "right", "a", "b", "c"]
SIX_BUTTON_COMMANDS = THREE_BUTTON_COMMANDS + [
    "x",
    "y",
    "z",
    # "mode" # Having difficulties with the mode button, so avoiding for now
]


class MegaDriveModule(HexpansionModule):
    VID, PID = 0xCAFE, 0x5E6A
    COMMAND_OPTIONS = THREE_BUTTON_COMMANDS

    @classmethod
    def decorate(cls, command):
        # Every MegaDrive command is a button, so always add a press verb.
        arrow_name = BUTTON_ARROW_NAMES.get(command)
        glyph = symbols["arrows"][arrow_name] if arrow_name else command
        return "{} {}".format(random_verb(PRESS_VERBS), glyph)

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

    def _get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        if button.group != "SegaController":
            return None

        six_button_map = {"d": "x", "e": "y", "f": "z"}
        value = button.name.lower()
        return six_button_map.get(value, value)