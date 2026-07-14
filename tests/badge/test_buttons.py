"""
is_cancel() must recognise the cancel button as current tildagonOS represents
it: the event carries the physical frontboard button, whose `parents` list
holds the logical BUTTON_TYPES["CANCEL"] (multi-inheritance introduced in
firmware commit 8aa7bd8 — the change that silently broke the previous
`button.parent` name check).

The unit tests run against the conftest stub. The differential test execs the
*genuine* firmware events/input.py out of a badge-2024-software checkout
(env BADGE_REPO, default ~/emf/badge-2024-software), so a future firmware
change to Button internals fails here instead of silently on the badge.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from events.input import Button, BUTTON_TYPES
from badge.buttons import is_cancel

BADGE_REPO = Path(os.environ.get("BADGE_REPO", Path.home() / "emf" / "badge-2024-software"))


def _event(button):
    ev = MagicMock()
    ev.button = button
    return ev


class TestIsCancelStub(unittest.TestCase):
    def test_physical_cancel_button(self):
        f = Button("F", "TwentyTwentyFour",
                   [BUTTON_TYPES["CANCEL"], Button("F", "Frontboard")])
        self.assertTrue(is_cancel(_event(f)))

    def test_logical_cancel_button_itself(self):
        self.assertTrue(is_cancel(_event(BUTTON_TYPES["CANCEL"])))

    def test_confirm_is_not_cancel(self):
        c = Button("C", "TwentyTwentyFour",
                   [BUTTON_TYPES["CONFIRM"], Button("C", "Frontboard")])
        self.assertFalse(is_cancel(_event(c)))

    def test_parentless_button_is_not_cancel(self):
        self.assertFalse(is_cancel(_event(Button("TOUCH1", "TwentyTwentySix"))))


@unittest.skipUnless((BADGE_REPO / ".git").exists(),
                     "no badge-2024-software checkout at {}".format(BADGE_REPO))
class TestIsCancelAgainstRealFirmware(unittest.TestCase):
    """Differential: our stub's assumptions vs the genuine firmware classes."""

    def setUp(self):
        source = (BADGE_REPO / "modules" / "events" / "input.py").read_text()
        self.fw = {"__name__": "firmware_input"}
        exec(compile(source, "modules/events/input.py", "exec"), self.fw)

    def test_cancel_detected_on_real_button(self):
        cancel = self.fw["BUTTON_TYPES"]["CANCEL"]
        f = self.fw["Button"]("F", "TwentyTwentyFour",
                              [cancel, self.fw["Button"]("F", "Frontboard")])
        self.assertTrue(is_cancel(_event(f)))

    def test_confirm_not_detected_on_real_button(self):
        confirm = self.fw["BUTTON_TYPES"]["CONFIRM"]
        c = self.fw["Button"]("C", "TwentyTwentyFour",
                              [confirm, self.fw["Button"]("C", "Frontboard")])
        self.assertFalse(is_cancel(_event(c)))


if __name__ == "__main__":
    unittest.main()
