#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("room", Path(__file__).parent / "room.py")
_room_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_room_mod)

Room = _room_mod.Room
ROUND_DURATION_S = _room_mod.ROUND_DURATION_S
COLOURS = _room_mod.COLOURS
GPS_CAPS = {"GPS": ("move 5m away",)}


class TestJoin(unittest.TestCase):
    def setUp(self):
        self.room = Room(1)

    def test_join_assigns_colour(self):
        data = self.room.join("badge-a", {})
        self.assertIsNotNone(data["colour"])

    def test_two_badges_get_different_colours(self):
        a = self.room.join("badge-a", {})
        b = self.room.join("badge-b", {})
        self.assertNotEqual(a["colour"], b["colour"])

    def test_join_is_waiting_state(self):
        data = self.room.join("badge-a", {})
        self.assertEqual(data["room_state"], "waiting")

    def test_waiting_state_has_no_assignment(self):
        data = self.room.join("badge-a", {})
        self.assertIsNone(data["assignment"])


class TestStartRound(unittest.TestCase):
    def setUp(self):
        self.room = Room(1)
        self.room.join("badge-a", GPS_CAPS)

    def test_start_round_transitions_to_in_round(self):
        data = self.room.start_round("badge-a")
        self.assertEqual(data["room_state"], "in-round")

    def test_start_round_unknown_badge_returns_error(self):
        data = self.room.start_round("nobody")
        self.assertIn("error", data)

    def test_cannot_start_twice(self):
        self.room.start_round("badge-a")
        data = self.room.start_round("badge-a")
        self.assertIn("error", data)

    def test_poll_in_round_yields_assignment(self):
        self.room.start_round("badge-a")
        data = self.room.poll("badge-a", GPS_CAPS)
        self.assertIsNotNone(data["assignment"])

    def test_assignment_stable_across_polls(self):
        self.room.start_round("badge-a")
        first = self.room.poll("badge-a", GPS_CAPS)["assignment"]["id"]
        second = self.room.poll("badge-a", GPS_CAPS)["assignment"]["id"]
        self.assertEqual(first, second)


class TestInstructionSelection(unittest.TestCase):
    def setUp(self):
        self.room = Room(1)
        self.room.join("badge-a", GPS_CAPS)
        self.room.join("badge-b", GPS_CAPS)
        self.room.start_round("badge-a")
        # seed assignments for both badges
        self.room.poll("badge-a", GPS_CAPS)
        self.room.poll("badge-b", GPS_CAPS)

    def test_instruction_never_targets_own_badge_in_multiplayer(self):
        my_colour = self.room.poll("badge-a", GPS_CAPS)["colour"]
        for _ in range(40):
            display = self.room.poll("badge-a", GPS_CAPS)["display"]
            if display:
                self.assertNotEqual(display["target_colour"], my_colour)

    def test_solo_badge_receives_own_instruction(self):
        room = Room(2)
        room.join("solo", GPS_CAPS)
        room.start_round("solo")
        data = room.poll("solo", GPS_CAPS)
        self.assertIsNotNone(data["display"])


class TestScoring(unittest.TestCase):
    def _setup_with_assignment(self, room_id=1):
        room = Room(room_id)
        room.join("badge-a", GPS_CAPS)
        room.start_round("badge-a")
        data = room.poll("badge-a", GPS_CAPS)
        return room, data.get("assignment")

    def test_passed_result_increments_score(self):
        room, assignment = self._setup_with_assignment()
        if not assignment:
            self.skipTest("no assignment")
        result = {"assignment_id": assignment["id"], "status": "passed"}
        data = room.poll("badge-a", GPS_CAPS, result=result)
        self.assertEqual(data["scores"]["passed"], 1)

    def test_failed_result_increments_score(self):
        room, assignment = self._setup_with_assignment(2)
        if not assignment:
            self.skipTest("no assignment")
        result = {"assignment_id": assignment["id"], "status": "failed"}
        data = room.poll("badge-a", GPS_CAPS, result=result)
        self.assertEqual(data["scores"]["failed"], 1)

    def test_wrong_assignment_id_is_ignored(self):
        room, _ = self._setup_with_assignment(3)
        result = {"assignment_id": "stale-id", "status": "passed"}
        data = room.poll("badge-a", GPS_CAPS, result=result)
        self.assertEqual(data["scores"]["passed"], 0)

    def test_per_badge_scores_tracked_by_colour(self):
        room, assignment = self._setup_with_assignment(4)
        if not assignment:
            self.skipTest("no assignment")
        my_colour = room.poll("badge-a", GPS_CAPS)["colour"]
        result = {"assignment_id": assignment["id"], "status": "passed"}
        data = room.poll("badge-a", GPS_CAPS, result=result)
        self.assertEqual(data["badge_scores"][my_colour]["passed"], 1)


class TestStateTransitions(unittest.TestCase):
    def test_round_expires_after_duration(self):
        room = Room(1)
        room.join("badge-a", GPS_CAPS)
        room.start_round("badge-a")
        room._round_started_at -= ROUND_DURATION_S + 1
        data = room.poll("badge-a", GPS_CAPS)
        self.assertEqual(data["room_state"], "finished")

    def test_all_badges_dismiss_returns_to_waiting(self):
        room = Room(1)
        room.join("badge-a", GPS_CAPS)
        room.join("badge-b", GPS_CAPS)
        room.start_round("badge-a")
        room._round_started_at -= ROUND_DURATION_S + 1
        room.poll("badge-a", GPS_CAPS)  # triggers expiry
        room.dismiss_score("badge-a")
        data = room.dismiss_score("badge-b")
        self.assertEqual(data["room_state"], "waiting")

    def test_partial_dismiss_stays_finished(self):
        room = Room(1)
        room.join("badge-a", GPS_CAPS)
        room.join("badge-b", GPS_CAPS)
        room.start_round("badge-a")
        room._round_started_at -= ROUND_DURATION_S + 1
        room.poll("badge-a", GPS_CAPS)
        data = room.dismiss_score("badge-a")
        self.assertEqual(data["room_state"], "finished")

    def test_last_badge_leave_resets_room(self):
        room = Room(1)
        room.join("badge-a", GPS_CAPS)
        room.start_round("badge-a")
        room.leave("badge-a")
        data = room.join("badge-a", GPS_CAPS)
        self.assertEqual(data["room_state"], "waiting")
        self.assertEqual(data["scores"]["passed"], 0)


if __name__ == "__main__":
    unittest.main()
