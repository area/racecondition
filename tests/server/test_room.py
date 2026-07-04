#!/usr/bin/env python3
import unittest

# server/ is placed on sys.path by tests/server/conftest.py
import room as _room_mod
from room import Room, ROUND_DURATION_S, COLOURS, MAX_BADGES
from leaderboard import SqliteLeaderboard

GPS_CAPS = [{"module": "GPS", "commands": ["move 5m away"]}]


def _room(room_id=1):
    return Room(room_id, leaderboard=SqliteLeaderboard(":memory:"))


class TestReadyToggle(unittest.TestCase):
    def setUp(self):
        self.room = _room(1)
        self.room.join("badge-a", {})
        self.room.join("badge-b", {})

    def _players(self, badge_id):
        return {p["colour"]: p for p in self.room.poll(badge_id, None)["players"]}

    def test_unready_removes_readiness(self):
        self.room.start_round("badge-a")  # only one of two: stays waiting
        data = self.room.unready("badge-a")
        self.assertEqual(data["ready_count"], 0)
        self.assertEqual(data["room_state"], "waiting")

    def test_ready_changes_reach_players_payload(self):
        self.room.start_round("badge-a")
        colour_a = self.room.poll("badge-a", None)["colour"]
        players = self._players("badge-b")
        self.assertTrue(players[colour_a]["ready"])
        self.room.unready("badge-a")
        players = self._players("badge-b")
        self.assertFalse(players[colour_a]["ready"])

    def test_unready_when_in_round_is_an_error(self):
        self.room.start_round("badge-a")
        self.room.start_round("badge-b")  # all ready: round starts
        data = self.room.unready("badge-a")
        self.assertIn("error", data)

    def test_unready_when_not_ready_is_a_noop(self):
        data = self.room.unready("badge-a")
        self.assertEqual(data["ready_count"], 0)

    def test_undismiss_holds_the_score_screen_open(self):
        self.room.start_round("badge-a")
        self.room.start_round("badge-b")
        self.room._round_started_at -= ROUND_DURATION_S  # expire the round
        self.room.poll("badge-a", None)
        self.room.dismiss_score("badge-a")
        self.room.undismiss_score("badge-a")
        # badge-b dismissing alone must not return the room to waiting.
        data = self.room.dismiss_score("badge-b")
        self.assertEqual(data["room_state"], "finished")
        data = self.room.dismiss_score("badge-a")
        self.assertEqual(data["room_state"], "waiting")


class TestJoin(unittest.TestCase):
    def setUp(self):
        self.room = _room(1)

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


class TestPollReAdd(unittest.TestCase):
    """A poll from an unknown badge (e.g. stale-pruned but its websocket
    survived) re-adds it, so it must respect the same capacity invariant as
    join — otherwise a full room grows past MAX_BADGES with duplicate colours."""

    def setUp(self):
        self.room = _room(1)

    def _fill_room(self):
        for i in range(MAX_BADGES):
            self.room.join("badge-{}".format(i), {})

    def test_poll_readds_unknown_badge_when_space(self):
        self.room.join("badge-a", {})
        data = self.room.poll("badge-b", GPS_CAPS)
        self.assertNotIn("error", data)
        self.assertEqual(data["badge_count"], 2)

    def test_poll_from_unknown_badge_when_full_returns_error(self):
        self._fill_room()
        data = self.room.poll("badge-late", GPS_CAPS)
        self.assertIn("error", data)
        colours = [b["colour"] for b in self.room.admin_snapshot()["badges"]]
        self.assertEqual(len(colours), len(set(colours)))

    def test_poll_from_known_badge_when_full_is_fine(self):
        self._fill_room()
        data = self.room.poll("badge-0", GPS_CAPS)
        self.assertNotIn("error", data)


class TestStartRound(unittest.TestCase):
    def setUp(self):
        self.room = _room(1)
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
        self.room = _room(1)
        self.room.join("badge-a", GPS_CAPS)
        self.room.join("badge-b", GPS_CAPS)
        self.room.start_round("badge-a")
        self.room.start_round("badge-b")
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
        room = _room(2)
        room.join("solo", GPS_CAPS)
        room.start_round("solo")
        data = room.poll("solo", GPS_CAPS)
        self.assertIsNotNone(data["display"])

    def test_instruction_stable_across_polls_with_three_badges(self):
        room = _room(3)
        room.join("badge-a", GPS_CAPS)
        room.join("badge-b", GPS_CAPS)
        room.join("badge-c", GPS_CAPS)
        room.start_round("badge-a")
        room.start_round("badge-b")
        room.start_round("badge-c")
        room.poll("badge-a", GPS_CAPS)
        room.poll("badge-b", GPS_CAPS)
        room.poll("badge-c", GPS_CAPS)
        first = room.poll("badge-a", GPS_CAPS)["display"]
        if first is None:
            self.skipTest("no display")
        first_colour = first["target_colour"]
        self.assertIsNotNone(first_colour)
        for _ in range(20):
            display = room.poll("badge-a", GPS_CAPS)["display"]
            if display is not None:
                self.assertEqual(display["target_colour"], first_colour)


class TestScoring(unittest.TestCase):
    def _setup_with_assignment(self, room_id=1):
        room = _room(room_id)
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
        room, assignment = self._setup_with_assignment(6)
        if not assignment:
            self.skipTest("no assignment")
        my_colour = room.poll("badge-a", GPS_CAPS)["colour"]
        result = {"assignment_id": assignment["id"], "status": "passed"}
        data = room.poll("badge-a", GPS_CAPS, result=result)
        self.assertEqual(data["badge_scores"][my_colour]["passed"], 1)

    def test_module_scores_tracked_on_pass(self):
        room, assignment = self._setup_with_assignment(7)
        if not assignment:
            self.skipTest("no assignment")
        result = {"assignment_id": assignment["id"], "status": "passed"}
        room.poll("badge-a", GPS_CAPS, result=result)
        self.assertEqual(room._module_scores.get("GPS", {}).get("passed"), 1)
        self.assertEqual(room._module_scores.get("GPS", {}).get("failed", 0), 0)

    def test_module_scores_tracked_on_fail(self):
        room, assignment = self._setup_with_assignment(8)
        if not assignment:
            self.skipTest("no assignment")
        result = {"assignment_id": assignment["id"], "status": "failed"}
        room.poll("badge-a", GPS_CAPS, result=result)
        self.assertEqual(room._module_scores.get("GPS", {}).get("failed"), 1)
        self.assertEqual(room._module_scores.get("GPS", {}).get("passed", 0), 0)

    def test_module_scores_tracked_on_timeout(self):
        room, assignment = self._setup_with_assignment(9)
        if not assignment:
            self.skipTest("no assignment")
        room._badges["badge-a"].assignment.issued_at -= _room_mod.ASSIGNMENT_TIMEOUT_S + 1
        room.poll("badge-a", GPS_CAPS)  # triggers timeout
        self.assertEqual(room._module_scores.get("GPS", {}).get("failed"), 1)

    def test_module_scores_and_badge_scores_in_leaderboard_entry(self):
        lb = SqliteLeaderboard(":memory:")
        room = Room(10, leaderboard=lb)
        room.join("badge-a", GPS_CAPS)
        room.start_round("badge-a")
        data = room.poll("badge-a", GPS_CAPS)
        assignment = data.get("assignment")
        if assignment:
            result = {"assignment_id": assignment["id"], "status": "passed"}
            room.poll("badge-a", GPS_CAPS, result=result)
        room._round_started_at -= ROUND_DURATION_S + 1
        room.poll("badge-a", GPS_CAPS)  # triggers _record_score
        self.assertEqual(len(lb.entries()), 1)
        entry = lb.entries()[0]
        self.assertIn("module_results", entry)
        self.assertIn("badge_scores", entry)
        self.assertIsInstance(entry["module_results"], dict)
        self.assertIsInstance(entry["badge_scores"], dict)


class TestStateTransitions(unittest.TestCase):
    def test_round_expires_after_duration(self):
        room = _room(1)
        room.join("badge-a", GPS_CAPS)
        room.start_round("badge-a")
        room._round_started_at -= ROUND_DURATION_S + 1
        data = room.poll("badge-a", GPS_CAPS)
        self.assertEqual(data["room_state"], "finished")

    def test_all_badges_dismiss_returns_to_waiting(self):
        room = _room(1)
        room.join("badge-a", GPS_CAPS)
        room.join("badge-b", GPS_CAPS)
        room.start_round("badge-a")
        room.start_round("badge-b")
        room._round_started_at -= ROUND_DURATION_S + 1
        room.poll("badge-a", GPS_CAPS)  # triggers expiry
        room.dismiss_score("badge-a")
        data = room.dismiss_score("badge-b")
        self.assertEqual(data["room_state"], "waiting")

    def test_partial_dismiss_stays_finished(self):
        room = _room(1)
        room.join("badge-a", GPS_CAPS)
        room.join("badge-b", GPS_CAPS)
        room.start_round("badge-a")
        room.start_round("badge-b")
        room._round_started_at -= ROUND_DURATION_S + 1
        room.poll("badge-a", GPS_CAPS)
        data = room.dismiss_score("badge-a")
        self.assertEqual(data["room_state"], "finished")

    def test_last_badge_leave_resets_room(self):
        room = _room(1)
        room.join("badge-a", GPS_CAPS)
        room.start_round("badge-a")
        room.leave("badge-a")
        data = room.join("badge-a", GPS_CAPS)
        self.assertEqual(data["room_state"], "waiting")
        self.assertEqual(data["scores"]["passed"], 0)


class TestAssignmentTimeoutRamp(unittest.TestCase):
    def setUp(self):
        self.room = _room(1)
        self.room.join("badge-a", GPS_CAPS)
        self.room.start_round("badge-a")
        self.start = _room_mod.ASSIGNMENT_TIMEOUT_S
        self.floor = _room_mod.ASSIGNMENT_TIMEOUT_FLOOR_S

    def test_timeout_starts_at_max(self):
        self.assertAlmostEqual(self.room._assignment_timeout(), self.start, places=1)

    def test_timeout_at_midround_is_halfway(self):
        self.room._round_started_at -= ROUND_DURATION_S / 2
        self.assertAlmostEqual(self.room._assignment_timeout(), (self.start + self.floor) / 2, places=1)

    def test_timeout_floors_at_round_end(self):
        self.room._round_started_at -= ROUND_DURATION_S
        self.assertAlmostEqual(self.room._assignment_timeout(), self.floor, places=1)

    def test_timeout_never_below_floor(self):
        self.room._round_started_at -= ROUND_DURATION_S * 2  # well past the end
        self.assertEqual(self.room._assignment_timeout(), self.floor)

    def test_issued_assignment_carries_ramped_timeout(self):
        # Just before the round ends, a freshly issued assignment uses ~the floor.
        self.room._round_started_at -= ROUND_DURATION_S - 1
        self.room._badges["badge-a"].assignment = None
        assignment = self.room.poll("badge-a", GPS_CAPS).get("assignment")
        self.assertIsNotNone(assignment)
        self.assertAlmostEqual(assignment["timeout_s"], self.floor, places=1)


class TestPollIfChanged(unittest.TestCase):
    """The websocket idle tick: nothing to push unless the room's generation
    moved or a time deadline (round end, assignment timeout) passed."""

    def setUp(self):
        self.room = _room(1)
        self.room.join("badge-a", GPS_CAPS)
        self.room.join("badge-b", GPS_CAPS)

    def _settle(self, badge_id, gen=None):
        # Polls until quiet: a fall-through poll can itself bump the
        # generation (issuing an assignment), needing one more pass.
        for _ in range(5):
            state, gen = self.room.poll_if_changed(badge_id, gen)
            if state is None:
                return gen
        self.fail("room never settled")

    def _start_round_settled(self):
        self.room.start_round("badge-a")
        self.room.start_round("badge-b")
        self.room.poll("badge-a", None)
        self.room.poll("badge-b", None)  # both assignments issued
        return self._settle("badge-a")

    def test_idle_room_returns_none(self):
        gen = self._settle("badge-a")
        state, gen_after = self.room.poll_if_changed("badge-a", gen)
        self.assertIsNone(state)
        self.assertEqual(gen_after, gen)

    def test_idle_tick_refreshes_liveness(self):
        gen = self._settle("badge-a")
        self.room._badges["badge-a"].last_seen -= _room_mod.STALE_BADGE_SECONDS
        self.room.poll_if_changed("badge-a", gen)
        # A teammate's poll prunes stale badges; badge-a must survive it.
        self.room.poll("badge-b", None)
        self.assertIn("badge-a", self.room._badges)

    def test_teammate_readying_up_is_pushed(self):
        gen = self._settle("badge-a")
        self.room.start_round("badge-b")
        state, _ = self.room.poll_if_changed("badge-a", gen)
        self.assertIsNotNone(state)
        self.assertEqual(state["ready_count"], 1)

    def test_teammate_result_is_pushed(self):
        gen = self._start_round_settled()
        assignment = self.room.poll("badge-b", None)["assignment"]
        gen = self._settle("badge-a", gen)
        self.room.poll("badge-b", None,
                       result={"assignment_id": assignment["id"], "status": "passed"})
        state, _ = self.room.poll_if_changed("badge-a", gen)
        self.assertIsNotNone(state)
        self.assertEqual(state["scores"]["passed"], 1)

    def test_assignment_timeout_deadline_wakes_idle_room(self):
        gen = self._start_round_settled()
        slot = self.room._badges["badge-b"]
        slot.assignment.issued_at -= slot.assignment.timeout_s + 1
        state, _ = self.room.poll_if_changed("badge-a", gen)
        self.assertIsNotNone(state)
        # The sweep failed badge-b's assignment even though badge-a polled.
        self.assertEqual(state["scores"]["failed"], 1)

    def test_round_expiry_deadline_wakes_idle_room(self):
        gen = self._start_round_settled()
        self.room._round_started_at -= ROUND_DURATION_S + 1
        state, _ = self.room.poll_if_changed("badge-a", gen)
        self.assertIsNotNone(state)
        self.assertEqual(state["room_state"], "finished")

    def test_admin_hurry_wakes_idle_room(self):
        gen = self._start_round_settled()
        self.room.set_timer(5)
        state, _ = self.room.poll_if_changed("badge-a", gen)
        self.assertIsNotNone(state)
        self.assertLessEqual(state["time_remaining_s"], 5.0)

    def test_unknown_badge_falls_through_to_readd(self):
        gen = self._settle("badge-a")
        state, _ = self.room.poll_if_changed("badge-new", gen)
        self.assertIsNotNone(state)
        self.assertIn("badge-new", self.room._badges)


if __name__ == "__main__":
    unittest.main()
