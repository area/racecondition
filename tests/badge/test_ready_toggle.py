"""Any physical button toggles readiness on the waiting and finished screens;
the 2026 touch pads (same ButtonDown group) do not."""

import unittest
from unittest.mock import MagicMock, patch

from badge.app import RaceConditionApp
from events.input import Button, BUTTON_TYPES


def _make_app():
    with patch.object(RaceConditionApp, "_scan"):
        a = RaceConditionApp(room_client=MagicMock())
        a._finish_init()
        return a


def _physical_event(name="A"):
    # Shaped like the firmware builds a face/joystick button: a logical
    # BUTTON_TYPES direction sits in its ancestry.
    event = MagicMock()
    event.button = Button(name, "TwentyTwentySix",
                          [BUTTON_TYPES["UP"], Button(name, "Frontboard")])
    return event


def _touch_pad_event(name="TOUCH05"):
    # Firmware builds touch pads with no logical parent (frontboards/twentysix.py).
    event = MagicMock()
    event.button = Button(name, "TwentyTwentySix")
    return event


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


class TestReadyOnlyPhysicalButtons(unittest.TestCase):
    """Touch pads must not ready or dismiss, even though they arrive as
    ButtonDown events in the same group as the physical buttons."""

    def setUp(self):
        self.app = _make_app()
        self.app.session.start_room(1)
        self.app.session.set_room_state("waiting")

    def test_face_button_readies(self):
        self.app._on_button_down(_physical_event("A"))
        self.assertTrue(self.app.session.is_ready)
        self.assertIn({"action": "start"}, self.app.net.outbox)

    def test_joystick_readies(self):
        self.app._on_button_down(_physical_event("JOYUP"))
        self.assertTrue(self.app.session.is_ready)

    def test_touch_pad_does_not_ready(self):
        self.app._on_button_down(_touch_pad_event("TOUCH05"))
        self.assertFalse(self.app.session.is_ready)
        self.assertEqual(self.app.net.outbox, [])

    def test_face_button_dismisses_finished_score(self):
        self.app.net.alive = True
        self.app.session.set_room_state("finished")
        self.app._on_button_down(_physical_event("A"))
        self.assertIn({"action": "dismiss"}, self.app.net.outbox)

    def test_touch_pad_does_not_dismiss_finished_score(self):
        self.app.net.alive = True
        self.app.session.set_room_state("finished")
        self.app._on_button_down(_touch_pad_event("TOUCH05"))
        self.assertEqual(self.app.net.outbox, [])
