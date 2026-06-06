import ast
import inspect
import unittest
from unittest.mock import MagicMock

from app.session import GameSession
from app.hexpansion.base import CommandStatus
import app.app as _app_module


def _uses_capitalize(fn):
    src = inspect.getsource(fn)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and node.attr == "capitalize":
            return True
    return False


class TestRoomStateProperties(unittest.TestCase):
    def setUp(self):
        self.s = GameSession()

    def test_not_in_game_initially(self):
        self.assertFalse(self.s.in_game)
        self.assertFalse(self.s.in_round)

    def test_in_game_after_start_room(self):
        self.s.start_room(3)
        self.assertTrue(self.s.in_game)

    def test_waiting_is_not_in_round(self):
        self.s.start_room(3)
        self.assertFalse(self.s.in_round)

    def test_in_round_after_set_room_state(self):
        self.s.start_room(3)
        self.s.set_room_state("in-round")
        self.assertTrue(self.s.in_round)

    def test_stop_room_clears_in_game(self):
        self.s.start_room(3)
        self.s.stop_room()
        self.assertFalse(self.s.in_game)

    def test_set_room_state_waiting_clears_assignment(self):
        self.s.start_room(3)
        self.s.set_room_state("in-round")
        self.s.set_assignment(MagicMock(), "id-1", "press a")
        self.s.set_room_state("waiting")
        self.assertIsNone(self.s.expected_module)
        self.assertIsNone(self.s.expected_command)

    def test_transition_to_waiting_clears_display(self):
        self.s.start_room(3)
        self.s.set_room_state("in-round")
        self.s.set_display({"module": "GPS", "command": "move 5m away", "target_colour": None})
        self.s.set_room_state("waiting")
        self.assertIsNone(self.s.display_module_name)

    def test_set_room_state_no_op_when_same(self):
        self.s.start_room(3)
        self.s.set_assignment(MagicMock(), "id-1", "press a")
        self.s.set_room_state("waiting")  # same as current — should clear assignment
        self.s.set_assignment(MagicMock(), "id-2", "press b")
        self.s.set_room_state("waiting")  # same again — should be no-op
        self.assertIsNotNone(self.s.expected_module)  # assignment preserved on no-op


class TestBuildResult(unittest.TestCase):
    def setUp(self):
        self.s = GameSession()
        self.module = MagicMock()
        self.module.FRIENDLY_NAME = "GPS"
        self.s.set_assignment(self.module, "id-123", "move 5m away")

    def test_passed_returns_correct_dict(self):
        result = self.s.build_result(CommandStatus.PASSED)
        self.assertEqual(result, {
            "assignment_id": "id-123",
            "status": "passed",
            "module": "GPS",
            "command": "move 5m away",
        })

    def test_failed_returns_correct_dict(self):
        result = self.s.build_result(CommandStatus.FAILED)
        self.assertEqual(result["status"], "failed")

    def test_passed_increments_local_score(self):
        self.s.build_result(CommandStatus.PASSED)
        self.assertEqual(self.s.score_pass, 1)

    def test_failed_increments_local_score(self):
        self.s.build_result(CommandStatus.FAILED)
        self.assertEqual(self.s.score_fail, 1)

    def test_build_result_clears_assignment(self):
        self.s.build_result(CommandStatus.PASSED)
        self.assertIsNone(self.s.expected_module)
        self.assertIsNone(self.s.expected_command_id)

    def test_no_module_returns_none(self):
        s = GameSession()
        self.assertIsNone(s.build_result(CommandStatus.PASSED))


class TestFormatRemaining(unittest.TestCase):
    def test_none_shows_placeholder(self):
        s = GameSession()
        self.assertEqual(s.format_remaining(), "--:--")

    def test_formats_correctly(self):
        s = GameSession()
        s.time_remaining_s = 90
        self.assertEqual(s.format_remaining(), "01:30")

    def test_formats_zero(self):
        s = GameSession()
        s.time_remaining_s = 0
        self.assertEqual(s.format_remaining(), "00:00")

    def test_clamps_negative_to_zero(self):
        s = GameSession()
        s.time_remaining_s = -5
        self.assertEqual(s.format_remaining(), "00:00")

    def test_full_two_minutes(self):
        s = GameSession()
        s.time_remaining_s = 120
        self.assertEqual(s.format_remaining(), "02:00")


class TestSetDisplay(unittest.TestCase):
    def setUp(self):
        self.s = GameSession()

    def test_none_clears_display(self):
        self.s.set_display({"module": "GPS", "command": "move", "target_colour": None})
        self.s.set_display(None)
        self.assertIsNone(self.s.display_module_name)
        self.assertIsNone(self.s.display_command)

    def test_colour_is_capitalised(self):
        self.s.set_display({"module": "GPS", "command": "move 5m away", "target_colour": "red"})
        self.assertEqual(self.s.display_target_colour, "Red")
        self.assertEqual(self.s.display_command, "move 5m away")

    def test_colour_capitalisation_uses_no_str_capitalize(self):
        # MicroPython does not implement str.capitalize() — verify the
        # display formatting uses upper()+slice, not capitalize().
        import ast, inspect, textwrap
        src = textwrap.dedent(inspect.getsource(GameSession.set_display))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "capitalize":
                self.fail("set_display uses str.capitalize() which is absent in MicroPython")

    def test_no_colour_shows_command_only(self):
        self.s.set_display({"module": "GPS", "command": "move 5m away", "target_colour": None})
        self.assertEqual(self.s.display_command, "move 5m away")

    def test_sets_module_name(self):
        self.s.set_display({"module": "MegaDrive", "command": "start", "target_colour": None})
        self.assertEqual(self.s.display_module_name, "MegaDrive")


class TestApplyPollResponse(unittest.TestCase):
    def setUp(self):
        self.s = GameSession()
        self.s.start_room(1)

    def _poll(self, **kwargs):
        base = {
            "room_state": "waiting",
            "badge_count": 1,
            "time_remaining_s": None,
            "scores": {"passed": 0, "failed": 0},
            "badge_scores": {},
            "colour": "red",
        }
        base.update(kwargs)
        return base

    def test_sets_room_state(self):
        self.s.apply_poll_response(self._poll(room_state="in-round"))
        self.assertTrue(self.s.in_round)

    def test_sets_badge_count(self):
        self.s.apply_poll_response(self._poll(badge_count=3))
        self.assertEqual(self.s.badge_count, 3)

    def test_sets_time_remaining(self):
        self.s.apply_poll_response(self._poll(time_remaining_s=90.0))
        self.assertEqual(self.s.time_remaining_s, 90.0)

    def test_sets_server_scores(self):
        self.s.apply_poll_response(self._poll(scores={"passed": 5, "failed": 2}))
        self.assertEqual(self.s.server_scores["passed"], 5)

    def test_sets_badge_scores(self):
        self.s.apply_poll_response(self._poll(badge_scores={"red": {"passed": 3, "failed": 1}}))
        self.assertEqual(self.s.badge_scores["red"]["passed"], 3)

    def test_clears_pending_result(self):
        self.s.pending_result = {"assignment_id": "x", "status": "passed"}
        self.s.apply_poll_response(self._poll())
        self.assertIsNone(self.s.pending_result)

    def test_returns_new_colour_when_changed(self):
        result = self.s.apply_poll_response(self._poll(colour="blue"))
        self.assertEqual(result, "blue")
        self.assertEqual(self.s.badge_colour, "blue")

    def test_returns_none_when_colour_unchanged(self):
        self.s.badge_colour = "red"
        result = self.s.apply_poll_response(self._poll(colour="red"))
        self.assertIsNone(result)

    def test_returns_none_when_no_colour_in_response(self):
        data = self._poll()
        del data["colour"]
        result = self.s.apply_poll_response(data)
        self.assertIsNone(result)

    def test_badge_scores_not_overwritten_when_absent(self):
        self.s.badge_scores = {"red": {"passed": 2, "failed": 0}}
        self.s.apply_poll_response(self._poll(badge_scores={}))
        self.assertEqual(self.s.badge_scores["red"]["passed"], 2)

    def test_transition_to_waiting_clears_assignment_fields(self):
        self.s.set_room_state("in-round")
        self.s.set_assignment(MagicMock(), "id-1", "move 5m away")
        self.s.apply_poll_response(self._poll(room_state="waiting"))
        self.assertIsNone(self.s.expected_module)


if __name__ == "__main__":
    unittest.main()
