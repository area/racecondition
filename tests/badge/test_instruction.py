import unittest

from badge.session import GameSession
from badge.hexpansion import decorate_command
from badge.hexpansion.MegaDrive import PRESS_VERBS as MEGADRIVE_VERBS
from badge.hexpansion.Tildagon2024 import PRESS_VERBS, GESTURE_PHRASES


class TestDecorateCommand(unittest.TestCase):
    def test_megadrive_button_gets_press_verb(self):
        # "a" -> "<verb> a" with a verb from the module's pool.
        for _ in range(20):
            phrase = decorate_command("MegaDrive", "a")
            verb, _, command = phrase.partition(" ")
            self.assertIn(verb, MEGADRIVE_VERBS)
            self.assertEqual(command, "a")

    def test_tildagon_button_gets_press_verb(self):
        phrase = decorate_command("Tildagon 2024", "b")
        verb, _, command = phrase.partition(" ")
        self.assertIn(verb, PRESS_VERBS)
        self.assertEqual(command, "b")

    def test_tildagon_gesture_is_a_whole_phrase(self):
        # Gestures read as a phrase, never "<verb> shake".
        for command in ("shake", "flip"):
            phrase = decorate_command("Tildagon 2024", command)
            self.assertIn(phrase, GESTURE_PHRASES[command])

    def test_gps_phrase_command_is_unchanged(self):
        self.assertEqual(decorate_command("GPS", "Move 10m away"), "Move 10m away")

    def test_unknown_module_is_unchanged(self):
        self.assertEqual(decorate_command("Nope", "a"), "a")

    def test_empty_command_is_unchanged(self):
        self.assertIsNone(decorate_command("MegaDrive", None))


class TestDisplayInstruction(unittest.TestCase):
    def setUp(self):
        self.s = GameSession()

    def test_set_display_decorates_command(self):
        self.s.set_display({"id": "a-1", "module": "MegaDrive", "command": "a", "target_colour": None})
        # Raw command preserved; display_instruction carries the decorated form.
        self.assertEqual(self.s.display_command, "a")
        self.assertTrue(self.s.display_instruction.endswith(" a"))

    def test_verb_is_stable_while_assignment_id_unchanged(self):
        # A full-state re-push of the same assignment must not re-roll the verb
        # (it would flicker mid-instruction on screen).
        self.s.set_display({"id": "a-1", "module": "MegaDrive", "command": "a", "target_colour": None})
        first = self.s.display_instruction
        for _ in range(20):
            self.s.set_display({"id": "a-1", "module": "MegaDrive", "command": "a", "target_colour": "red"})
            self.assertEqual(self.s.display_instruction, first)

    def test_verb_rerolls_on_new_assignment_of_same_command(self):
        # Same command, new assignment id -> verb is re-randomised. Over many
        # fresh ids the decorated phrase varies (it may coincidentally repeat).
        seen = set()
        for i in range(40):
            self.s.set_display({"id": "a-{}".format(i), "module": "MegaDrive", "command": "a", "target_colour": None})
            self.assertTrue(self.s.display_instruction.endswith(" a"))
            seen.add(self.s.display_instruction)
        self.assertGreater(len(seen), 1)

    def test_verb_rerolls_when_command_changes_without_id(self):
        # Fallback path: no id supplied, so a changed command re-rolls.
        self.s.set_display({"module": "MegaDrive", "command": "a", "target_colour": None})
        self.s.set_display({"module": "MegaDrive", "command": "b", "target_colour": None})
        self.assertTrue(self.s.display_instruction.endswith(" b"))

    def test_clear_display_resets_instruction(self):
        self.s.set_display({"id": "a-1", "module": "MegaDrive", "command": "a", "target_colour": None})
        self.s.set_display(None)
        self.assertIsNone(self.s.display_instruction)
        self.assertIsNone(self.s.display_assignment_id)


if __name__ == "__main__":
    unittest.main()
