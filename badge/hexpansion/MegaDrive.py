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
    "up_left": "north_west",
    "up_right": "north_east",
    "down_left": "south_west",
    "down_right": "south_east",
}

DIRECTIONS = frozenset(("up", "down", "left", "right"))

# A diagonal is satisfied only while both of its directions are held at once,
# which is exactly what a real D-pad does when you push into a corner. A cardinal
# is satisfied only while exactly its one direction is held — pushing into a
# corner holds two directions and so must not pass a single-direction command.
DIAGONAL_COMMANDS = {
    "up_left": frozenset(("up", "left")),
    "up_right": frozenset(("up", "right")),
    "down_left": frozenset(("down", "left")),
    "down_right": frozenset(("down", "right")),
}


SIX_BUTTON_ONLY = {"x", "y", "z", #"mode"
                   }

THREE_BUTTON_COMMANDS = [
    "start", "up", "down", "left", "right",
    "up_left", "up_right", "down_left", "down_right",
    "a", "b", "c",
]
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
        self._held = set()

    def set_command(self, command):
        # Start each command with a clean view of held buttons, so a press left
        # over from the previous command can't satisfy a fresh diagonal.
        self._held = set()
        return super().set_command(command)

    def on_button_down(self, event):
        button_name = self._get_button_name(event)
        if button_name is None:
            return
        if button_name in SIX_BUTTON_ONLY and not self.is_six_button:
            self.is_six_button = True
            self.COMMAND_OPTIONS = SIX_BUTTON_COMMANDS
        self._held.add(button_name)
        # Directional commands are judged in check_command against the full set of
        # held directions, so a corner press (two directions at once) can't latch a
        # cardinal before its partner press has arrived. Plain buttons latch here.
        if self._required_directions() is None and button_name == self.current_command:
            self.last_status = CommandStatus.PASSED

    def on_button_up(self, event):
        button_name = self._get_button_name(event)
        if button_name is None:
            return
        self._held.discard(button_name)

    def check_command(self):
        required = self._required_directions()
        if required is not None and (self._held & DIRECTIONS) == required:
            self.last_status = CommandStatus.PASSED
        return self.last_status

    def _required_directions(self):
        # The exact set of D-pad directions that must be held for the current
        # command, or None if the command isn't directional (a plain button).
        if self.current_command in DIRECTIONS:
            return frozenset((self.current_command,))
        return DIAGONAL_COMMANDS.get(self.current_command)

    def _get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        if button.group != "SegaController":
            return None

        six_button_map = {"d": "x", "e": "y", "f": "z"}
        value = button.name.lower()
        return six_button_map.get(value, value)