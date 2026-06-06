import time
import unittest
from unittest.mock import MagicMock, patch

from app.app import TildateamApp, TEST_SKIP_HOLD_MS
from app.hexpansion.base import CommandStatus


def _make_app(modules=None):
    room_client = MagicMock()
    with patch.object(TildateamApp, "_scan"):
        a = TildateamApp(room_client=room_client)
    a.connected_modules = modules or []
    return a


def _make_module(name, commands):
    m = MagicMock()
    m.FRIENDLY_NAME = name
    m.COMMAND_OPTIONS = commands
    m.check_command.return_value = CommandStatus.WAITING
    return m


def _make_button_event(name="a", group="TwentyTwentyFour"):
    btn = MagicMock()
    btn.name = name
    btn.parent = None
    ev = MagicMock()
    ev.button = btn
    return ev


def _cancel_event():
    btn = MagicMock()
    btn.name = "cancel"
    btn.parent = None
    ev = MagicMock()
    ev.button = btn
    return ev


class TestTestingModeEntry(unittest.TestCase):
    def test_menu_contains_test_modules(self):
        a = _make_app()
        self.assertIn("Test modules", a._menu_items())

    def test_no_modules_shows_notification_not_test_state(self):
        a = _make_app(modules=[])
        a._start_testing()
        self.assertIsNone(a._test_state)
        self.assertIsNotNone(a.notification)

    def test_with_modules_enters_command_state(self):
        m = _make_module("MegaDrive", ["a", "b"])
        a = _make_app(modules=[m])
        a._start_testing()
        self.assertEqual(a._test_state, "command")

    def test_first_command_set_on_module(self):
        m = _make_module("MegaDrive", ["a", "b"])
        a = _make_app(modules=[m])
        a._start_testing()
        m.set_command.assert_called_with("a")

    def test_menu_dismissed_on_enter(self):
        m = _make_module("MegaDrive", ["a"])
        a = _make_app(modules=[m])
        a._ensure_menu()
        a._start_testing()
        self.assertIsNone(a.menu)

    def test_items_cover_all_modules_and_commands(self):
        m1 = _make_module("MegaDrive", ["a", "b"])
        m2 = _make_module("GPS", ["move 5m away"])
        a = _make_app(modules=[m1, m2])
        a._start_testing()
        self.assertEqual(len(a._test_items), 3)
        self.assertEqual(a._test_items, [(m1, "a"), (m1, "b"), (m2, "move 5m away")])

    def test_no_network_requests_on_enter(self):
        m = _make_module("MegaDrive", ["a"])
        a = _make_app(modules=[m])
        a._start_testing()
        a.room_client.join_room.assert_not_called()
        a.room_client.poll.assert_not_called()


class TestTestingModeProgression(unittest.TestCase):
    def setUp(self):
        self.m = _make_module("MegaDrive", ["a", "b", "c"])
        self.a = _make_app(modules=[self.m])
        self.a._start_testing()

    def test_pass_advances_to_next_command(self):
        self.m.check_command.return_value = CommandStatus.PASSED
        self.a._update_testing()
        self.assertEqual(self.a._test_index, 1)
        self.m.set_command.assert_called_with("b")

    def test_pass_increments_passed_count(self):
        self.m.check_command.return_value = CommandStatus.PASSED
        self.a._update_testing()
        self.assertEqual(self.a._test_passed, 1)

    def test_waiting_does_not_advance(self):
        self.m.check_command.return_value = CommandStatus.WAITING
        self.a._update_testing()
        self.assertEqual(self.a._test_index, 0)

    def test_all_commands_pass_reaches_summary(self):
        self.m.check_command.return_value = CommandStatus.PASSED
        for _ in range(3):
            self.a._update_testing()
        self.assertEqual(self.a._test_state, "summary")
        self.assertEqual(self.a._test_passed, 3)
        self.assertEqual(self.a._test_skipped, 0)

    def test_button_routed_to_current_module(self):
        ev = _make_button_event("a")
        self.a._on_button_down(ev)
        self.m.on_button_down.assert_called_once_with(ev)


class TestTestingModeSkip(unittest.TestCase):
    def setUp(self):
        self.m = _make_module("MegaDrive", ["a", "b", "c"])
        self.a = _make_app(modules=[self.m])
        self.a._start_testing()

    def test_cancel_down_starts_hold_timer(self):
        self.a._on_button_down(_cancel_event())
        self.assertIsNotNone(self.a._test_cancel_hold_start)

    def test_cancel_up_clears_hold_timer(self):
        self.a._on_button_down(_cancel_event())
        self.a._on_button_up(_cancel_event())
        self.assertIsNone(self.a._test_cancel_hold_start)

    def test_short_cancel_forwards_to_module(self):
        self.a._on_button_down(_cancel_event())
        self.a._on_button_up(_cancel_event())
        self.m.on_button_down.assert_called_once()

    def test_short_cancel_does_not_skip(self):
        self.a._on_button_down(_cancel_event())
        self.a._update_testing()
        self.assertEqual(self.a._test_index, 0)
        self.assertEqual(self.a._test_skipped, 0)

    def test_held_cancel_skips_command(self):
        self.a._on_button_down(_cancel_event())
        self.a._test_cancel_hold_start = time.ticks_ms() - TEST_SKIP_HOLD_MS
        self.a._update_testing()
        self.assertEqual(self.a._test_index, 1)
        self.assertEqual(self.a._test_skipped, 1)

    def test_skip_all_reaches_summary(self):
        for _ in range(3):
            self.a._on_button_down(_cancel_event())
            self.a._test_cancel_hold_start = time.ticks_ms() - TEST_SKIP_HOLD_MS
            self.a._update_testing()
        self.assertEqual(self.a._test_state, "summary")
        self.assertEqual(self.a._test_skipped, 3)
        self.assertEqual(self.a._test_passed, 0)


class TestTestingModeSummary(unittest.TestCase):
    def _reach_summary(self, passed=0, skipped=3):
        m = _make_module("MegaDrive", ["a", "b", "c"])
        a = _make_app(modules=[m])
        a._start_testing()
        for _ in range(skipped):
            a._on_button_down(_cancel_event())
            a._test_cancel_hold_start = time.ticks_ms() - TEST_SKIP_HOLD_MS
            a._update_testing()
        return a

    def test_any_button_exits_summary(self):
        a = self._reach_summary()
        a._on_button_down(_make_button_event())
        self.assertIsNone(a._test_state)

    def test_exit_restores_menu_on_next_update(self):
        a = self._reach_summary()
        a._on_button_down(_make_button_event())
        self.assertIsNone(a._test_state)
        a.update(0)
        self.assertIsNotNone(a.menu)

    def test_cancel_up_does_not_exit_summary(self):
        a = self._reach_summary()
        a._on_button_up(_cancel_event())
        self.assertEqual(a._test_state, "summary")

    def test_session_not_modified(self):
        a = self._reach_summary()
        self.assertFalse(a.session.in_game)


if __name__ == "__main__":
    unittest.main()
