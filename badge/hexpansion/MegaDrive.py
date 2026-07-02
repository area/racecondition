import time

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
# (or plain button) is satisfied as soon as its button is in the held set, so a
# corner press latches the matching cardinal the moment that direction goes down.
DIAGONAL_COMMANDS = {
    "up_left": frozenset(("up", "left")),
    "up_right": frozenset(("up", "right")),
    "down_left": frozenset(("down", "left")),
    "down_right": frozenset(("down", "right")),
}


SIX_BUTTON_ONLY = {"x", "y", "z", #"mode"
                   }

COMBOS = {
    # Quarter-circle-forward + A ("Hadouken").
    "qcf_a": ["down", "down_right", "right", "a"],
    "qcb_b": ["down", "down_left", "left", "b"]
}

# Each step must follow the previous one within this window, else the combo
# resets to the start — it bounds how slow a motion can be while still reading as
# one fluid input. The whole attempt is separately bounded by the server's
# per-assignment timeout.
COMBO_STEP_MS = 600

THREE_BUTTON_COMMANDS = [
    "start", "up", "down", "left", "right",
    "up_left", "up_right", "down_left", "down_right",
    "a", "b", "c",
] + list(COMBOS)
SIX_BUTTON_COMMANDS = THREE_BUTTON_COMMANDS + [
    "x",
    "y",
    "z",
    # "mode" # Having difficulties with the mode button, so avoiding for now
]


class MegaDriveModule(HexpansionModule):
    VID, PID = 0x4291, 0x5E6A
    COMMAND_OPTIONS = THREE_BUTTON_COMMANDS

    @classmethod
    def decorate(cls, command):
        steps = COMBOS.get(command)
        if steps is not None:
            # Show the motion as its glyph sequence, e.g. "↓ ↘ → A", so the player
            # reads the whole combo at a glance.
            return " ".join(cls._token_glyph(token) for token in steps)
        # Every other MegaDrive command is a single button, so add a press verb.
        return "{} {}".format(random_verb(PRESS_VERBS), cls._token_glyph(command))

    @classmethod
    def _token_glyph(cls, token):
        arrow_name = BUTTON_ARROW_NAMES.get(token)
        if arrow_name:
            return symbols["arrows"][arrow_name]
        return token

    def __init__(self):
        super().__init__()
        self.is_six_button = False

    def reset(self):
        super().reset()
        self.is_six_button = False
        self.COMMAND_OPTIONS = THREE_BUTTON_COMMANDS
        self._held = set()
        self._combo_progress = 0
        self._combo_last_ms = 0

    def set_command(self, command):
        # Start each command with a clean view of held buttons, so a press left
        # over from the previous command can't satisfy a fresh diagonal, and a
        # combo always starts from its first step.
        self._held = set()
        self._combo_progress = 0
        self._combo_last_ms = time.ticks_ms()
        return super().set_command(command)

    def on_button_down(self, event):
        button_name = self._get_button_name(event)
        if button_name is None:
            return
        if button_name in SIX_BUTTON_ONLY and not self.is_six_button:
            self.is_six_button = True
            self.COMMAND_OPTIONS = SIX_BUTTON_COMMANDS
            print("[MegaDrive] switched to six-button mode")
        self._held.add(button_name)
        self._evaluate_command()

    def on_button_up(self, event):
        button_name = self._get_button_name(event)
        if button_name is None:
            return
        self._held.discard(button_name)
        self._evaluate_command()

    def _evaluate_command(self):
        # Judge the current command against the buttons held right now, on every
        # press and release rather than polling each frame in check_command. Once
        # PASSED it stays PASSED — a release can't un-latch it.
        steps = COMBOS.get(self.current_command)
        if steps is not None:
            self._advance_combo(steps)
        elif self._matches(self.current_command):
            print("[MegaDrive] command '{}' PASSED".format(self.current_command))
            self.last_status = CommandStatus.PASSED

    def _advance_combo(self, steps):
        # Advance-only matching: progress moves forward when the held buttons are
        # exactly the next step, and wrong or extra inputs are simply ignored. The
        # only way back is the per-step timeout — dawdle longer than COMBO_STEP_MS
        # and the motion resets to the start (the first step has no predecessor,
        # so it's never on the clock). Releases drive the directional steps (e.g.
        # letting go of "down" is what turns "down+right" into "right").
        now = time.ticks_ms()
        if self._combo_progress > 0 and time.ticks_diff(now, self._combo_last_ms) > COMBO_STEP_MS:
            self._combo_progress = 0
        if self._step_matches(steps[self._combo_progress]):
            self._combo_progress += 1
            self._combo_last_ms = now
            if self._combo_progress == len(steps):
                print("[MegaDrive] combo '{}' PASSED".format(self.current_command))
                self.last_status = CommandStatus.PASSED

    def _step_matches(self, token):
        # A combo's bare-direction step must be the EXACT set of D-pad directions
        # held — "right" requires "down" to have been released first — so a
        # "down -> down_right -> right" roll advances one step at a time. Diagonals
        # and buttons match the same way inside a combo as on their own.
        if token in DIRECTIONS:
            return (self._held & DIRECTIONS) == frozenset((token,))
        return self._matches(token)

    def _matches(self, token):
        # Shared by single commands and a combo's non-bare-direction steps: a
        # diagonal needs both its directions held; a cardinal or face button just
        # needs to be present in the held set, so a single-command cardinal latches
        # as soon as it's pressed, even mid-corner.
        diagonal = DIAGONAL_COMMANDS.get(token)
        if diagonal is not None:
            return (self._held & DIRECTIONS) == diagonal
        return token in self._held

    def _get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        if button.group != "SegaController":
            return None

        six_button_map = {"d": "x", "e": "y", "f": "z"}
        value = button.name.lower()
        return six_button_map.get(value, value)