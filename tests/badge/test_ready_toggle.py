"""Any button toggles readiness on the waiting and finished screens."""

import unittest
from unittest.mock import MagicMock, patch

from badge.app import RaceConditionApp


def _make_app():
    with patch.object(RaceConditionApp, "_scan"):
        a = RaceConditionApp(room_client=MagicMock())
        a._finish_init()
        return a


class TestWaitingReadyToggle(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.app.session.start_room(1)
        self.app.session.set_room_state("waiting")

    def test_press_when_unready_sends_start(self):
        self.app._start_round()
        self.assertIn({"action": "start"}, self.app.net.outbox)
        self.assertTrue(self.app.session.is_ready)

    def test_press_when_ready_sends_unready(self):
        self.app.session.is_ready = True
        self.app._start_round()
        self.assertIn({"action": "unready"}, self.app.net.outbox)
        self.assertFalse(self.app.session.is_ready)

    def test_double_press_round_trips(self):
        self.app._start_round()
        self.app._start_round()
        self.assertEqual(
            self.app.net.outbox,
            [{"action": "start"}, {"action": "unready"}],
        )

    def test_press_optimistically_updates_count_and_own_dot(self):
        session = self.app.session
        session.badge_count = 3
        session.ready_count = 1
        session.badge_colour = "red"
        session.players = [
            {"colour": "red", "ready": False},
            {"colour": "blue", "ready": True},
        ]
        self.app._start_round()
        self.assertEqual(session.ready_count, 2)
        self.assertTrue(session.players[0]["ready"])
        self.app._start_round()
        self.assertEqual(session.ready_count, 1)
        self.assertFalse(session.players[0]["ready"])
        self.assertTrue(session.players[1]["ready"])

    def test_optimistic_count_stays_within_bounds(self):
        session = self.app.session
        session.badge_count = 2
        session.ready_count = 2
        self.app._start_round()
        self.assertEqual(session.ready_count, 2)
        session.is_ready = True
        session.ready_count = 0
        self.app._start_round()
        self.assertEqual(session.ready_count, 0)


class TestFinishedDismissToggle(unittest.TestCase):
    def setUp(self):
        self.app = _make_app()
        self.app.session.start_room(1)
        self.app.session.set_room_state("finished")
        self.app.net.alive = True

    def test_press_when_undismissed_sends_dismiss(self):
        self.app._dismiss_score()
        self.assertIn({"action": "dismiss"}, self.app.net.outbox)
        self.assertTrue(self.app.session.is_dismissed)

    def test_press_when_dismissed_sends_undismiss(self):
        self.app.session.is_dismissed = True
        self.app._dismiss_score()
        self.assertIn({"action": "undismiss"}, self.app.net.outbox)
        self.assertFalse(self.app.session.is_dismissed)

    def test_press_optimistically_updates_dismissed_count(self):
        session = self.app.session
        session.badge_count = 3
        session.dismissed_count = 1
        self.app._dismiss_score()
        self.assertEqual(session.dismissed_count, 2)
        self.app._dismiss_score()
        self.assertEqual(session.dismissed_count, 1)

    def test_offline_fallback_still_advances_locally(self):
        self.app.net.alive = False
        self.app._dismiss_score()
        self.assertEqual(self.app.session.room_state, "waiting")
        self.assertEqual(self.app.net.outbox, [])
