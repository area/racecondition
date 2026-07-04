from .base import HexpansionModule, CommandStatus
from .verbs import random_verb

# Homophone pairs: the shouter sees the spelling, the typist only hears it.
# Commands ARE the words — the token sent to the server is the word itself.
WORDS = ("pores", "pours", "paws", "pause")

TYPE_VERBS = ("Type", "Key in", "Spell")

# The buffer only ever needs to hold the longest word plus room to notice
# junk immediately before it; bounding it keeps a round of frantic mashing
# from growing an unbounded string on the badge.
MAX_TYPED = 32


class KeyboardModule(HexpansionModule):
    # "keepdeck" keyboard hexpansion (see hexpansion-firmwares/0xbad3/0x4eeb).
    # Its firmware app emits ButtonDown events in group "Keyboard" with names
    # from the firmware's KEYBOARD_BUTTONS table: single characters for keys
    # that type something ("A".."Z", digits, punctuation — already shifted by
    # the driver) and words for modifiers ("SPACE", "BACKSPACE", "ENTER", …).
    VID, PID = 0xBAD3, 0x4EEB

    COMMAND_OPTIONS = list(WORDS)

    @classmethod
    def decorate(cls, command):
        return '{} "{}"'.format(random_verb(TYPE_VERBS), command)

    def reset(self):
        super().reset()
        self._typed = ""

    def set_command(self, command):
        # Each command starts from a blank buffer so leftovers from the
        # previous word can't complete this one.
        self._typed = ""
        return super().set_command(command)

    def on_button_down(self, event):
        key = self._get_key(event)
        if key is None or self.current_command is None:
            return
        if key == "BACKSPACE":
            self._typed = self._typed[:-1]
        elif key == "SPACE":
            self._typed += " "
        elif len(key) == 1:
            self._typed += key.lower()
        else:
            # ENTER, SHIFT, arrows, … don't type anything.
            return
        self._typed = self._typed[-MAX_TYPED:]
        # The word must be the latest thing typed, so a typo mid-word means
        # retyping the whole word (or backspacing to fix it) — but earlier
        # junk is forgiven. No word in WORDS contains another, so a wrong
        # homophone can never complete the right one.
        if self._typed.endswith(self.current_command):
            print("[Keyboard] typed '{}' PASSED".format(self.current_command))
            self.last_status = CommandStatus.PASSED

    def _get_key(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None
        # Key has to be from us
        if button.group != "Keyboard":
            return None
        return button.name
