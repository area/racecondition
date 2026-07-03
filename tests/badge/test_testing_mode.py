"""App-level integration tests for testing mode — verifies RaceConditionApp delegates
correctly to TestSession. Unit tests for TestSession itself live in test_test_session.py."""
import unittest
from unittest.mock import MagicMock, patch

from badge.app import RaceConditionApp


def _make_app(modules=None):
    room_client = MagicMock()
    with patch.object(RaceConditionApp, "_scan"):
        a = RaceConditionApp(room_client=room_client)
        # On the badge the heavy init is deferred to the first update tick so
        # a loading frame can render first; tests drive it directly.
        a._finish_init()
    a.module_registry._connected_modules = {
        m.friendly_name(): m for m in (modules or [])
    }
    return a


def _make_module(name, commands):
    m = MagicMock()
    m.friendly_name.return_value = name
    m.COMMAND_OPTIONS = commands
    m.get_capabilities.return_value = {"module": name, "commands": commands}
    return m


class TestTestingModeApp(unittest.TestCase):
    def test_menu_contains_test_modules(self):
        a = _make_app()
        self.assertIn("Test modules", a._main_menu_items())

    def test_no_modules_shows_notification_no_test_session(self):
        a = _make_app(modules=[])
        a._start_testing()
        self.assertIsNone(a._test_session)
        self.assertIsNotNone(a.notification)

    def test_with_modules_creates_test_session(self):
        m = _make_module("MegaDrive", ["a"])
        a = _make_app(modules=[m])
        a._start_testing()
        a._test_menu_select("MegaDrive", 0)
        self.assertIsNotNone(a._test_session)

    def test_no_network_requests_on_enter(self):
        m = _make_module("MegaDrive", ["a"])
        a = _make_app(modules=[m])
        a._start_testing()
        a.room_client.join_room.assert_not_called()
        a.room_client.poll.assert_not_called()

    def test_done_state_clears_test_session_on_update(self):
        m = _make_module("MegaDrive", ["a"])
        a = _make_app(modules=[m])
        a._start_testing()
        a._test_menu_select("MegaDrive", 0)
        a._test_session.state = "done"
        a.update(0)
        self.assertIsNone(a._test_session)

    def test_menu_restored_after_session_done(self):
        m = _make_module("MegaDrive", ["a"])
        a = _make_app(modules=[m])
        a._start_testing()
        a._test_menu_select("MegaDrive", 0)
        a._test_session.state = "done"
        a.update(0)   # clears test_session
        a.update(0)   # enters else branch → _ensure_menu
        self.assertIsNotNone(a.menu)

    def test_session_not_modified(self):
        m = _make_module("MegaDrive", ["a"])
        a = _make_app(modules=[m])
        a._start_testing()
        self.assertFalse(a.session.in_game)


if __name__ == "__main__":
    unittest.main()
