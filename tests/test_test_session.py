import time
import unittest
from unittest.mock import MagicMock

from app.test_session import TestSession, TEST_SKIP_HOLD_MS
from app.hexpansion.base import CommandStatus


def _make_module(name, commands):
    m = MagicMock()
    m.FRIENDLY_NAME = name
    m.COMMAND_OPTIONS = commands
    m.check_command.return_value = CommandStatus.WAITING
    return m


def _btn(name="a", group="TwentyTwentyFour"):
    btn = MagicMock()
    btn.name = name
    btn.parent = None
    ev = MagicMock()
    ev.button = btn
    return ev


def _cancel():
    return _btn("cancel")


class TestTestSessionInit(unittest.TestCase):
    def test_no_modules_state_is_done(self):
        ts = TestSession([])
        self.assertEqual(ts.state, "done")

    def test_with_modules_state_is_command(self):
        m = _make_module("MegaDrive", ["a", "b"])
        ts = TestSession([m])
        self.assertEqual(ts.state, "command")

    def test_first_command_set_on_module(self):
        m = _make_module("MegaDrive", ["a", "b"])
        TestSession([m])
        m.set_command.assert_called_with("a")

    def test_items_cover_all_modules_and_commands(self):
        m1 = _make_module("MegaDrive", ["a", "b"])
        m2 = _make_module("GPS", ["move 5m away"])
        ts = TestSession([m1, m2])
        self.assertEqual(ts.total, 3)
        self.assertEqual(ts._items, [(m1, "a"), (m1, "b"), (m2, "move 5m away")])

    def test_current_module_and_command(self):
        m = _make_module("MegaDrive", ["a", "b"])
        ts = TestSession([m])
        self.assertIs(ts.current_module, m)
        self.assertEqual(ts.current_command, "a")

    def test_index_and_totals_start_at_zero(self):
        m = _make_module("MegaDrive", ["a"])
        ts = TestSession([m])
        self.assertEqual(ts.index, 0)
        self.assertEqual(ts.passed, 0)
        self.assertEqual(ts.skipped, 0)


class TestTestSessionProgression(unittest.TestCase):
    def setUp(self):
        self.m = _make_module("MegaDrive", ["a", "b", "c"])
        self.ts = TestSession([self.m])

    def test_pass_advances_index(self):
        self.m.check_command.return_value = CommandStatus.PASSED
        self.ts.update()
        self.assertEqual(self.ts.index, 1)
        self.m.set_command.assert_called_with("b")

    def test_pass_increments_passed(self):
        self.m.check_command.return_value = CommandStatus.PASSED
        self.ts.update()
        self.assertEqual(self.ts.passed, 1)

    def test_waiting_does_not_advance(self):
        self.ts.update()
        self.assertEqual(self.ts.index, 0)

    def test_all_pass_reaches_summary(self):
        self.m.check_command.return_value = CommandStatus.PASSED
        for _ in range(3):
            self.ts.update()
        self.assertEqual(self.ts.state, "summary")
        self.assertEqual(self.ts.passed, 3)

    def test_button_routed_to_current_module(self):
        self.ts.on_button_down(_btn("a"))
        self.m.on_button_down.assert_called_once()

    def test_current_module_is_none_in_summary(self):
        self.m.check_command.return_value = CommandStatus.PASSED
        for _ in range(3):
            self.ts.update()
        self.assertIsNone(self.ts.current_module)
        self.assertIsNone(self.ts.current_command)


class TestTestSessionSkip(unittest.TestCase):
    def setUp(self):
        self.m = _make_module("MegaDrive", ["a", "b", "c"])
        self.ts = TestSession([self.m])

    def test_cancel_down_starts_hold(self):
        self.ts.on_button_down(_cancel())
        self.assertIsNotNone(self.ts._cancel_hold_start)

    def test_cancel_up_clears_hold(self):
        self.ts.on_button_down(_cancel())
        self.ts.on_button_up(_cancel())
        self.assertIsNone(self.ts._cancel_hold_start)

    def test_short_cancel_forwards_to_module(self):
        self.ts.on_button_down(_cancel())
        self.ts.on_button_up(_cancel())
        self.m.on_button_down.assert_called_once()

    def test_short_cancel_does_not_skip(self):
        self.ts.on_button_down(_cancel())
        self.ts.update()
        self.assertEqual(self.ts.index, 0)
        self.assertEqual(self.ts.skipped, 0)

    def test_held_cancel_skips(self):
        self.ts.on_button_down(_cancel())
        self.ts._cancel_hold_start = time.ticks_ms() - TEST_SKIP_HOLD_MS
        self.ts.update()
        self.assertEqual(self.ts.index, 1)
        self.assertEqual(self.ts.skipped, 1)

    def test_skip_all_reaches_summary(self):
        for _ in range(3):
            self.ts.on_button_down(_cancel())
            self.ts._cancel_hold_start = time.ticks_ms() - TEST_SKIP_HOLD_MS
            self.ts.update()
        self.assertEqual(self.ts.state, "summary")
        self.assertEqual(self.ts.skipped, 3)
        self.assertEqual(self.ts.passed, 0)


class TestTestSessionSummary(unittest.TestCase):
    def _reach_summary(self):
        m = _make_module("MegaDrive", ["a", "b", "c"])
        ts = TestSession([m])
        for _ in range(3):
            ts.on_button_down(_cancel())
            ts._cancel_hold_start = time.ticks_ms() - TEST_SKIP_HOLD_MS
            ts.update()
        return ts

    def test_any_button_transitions_to_done(self):
        ts = self._reach_summary()
        ts.on_button_down(_btn())
        self.assertEqual(ts.state, "done")

    def test_cancel_up_does_not_exit_summary(self):
        ts = self._reach_summary()
        ts.on_button_up(_cancel())
        self.assertEqual(ts.state, "summary")

    def test_update_in_summary_is_noop(self):
        ts = self._reach_summary()
        ts.update()
        self.assertEqual(ts.state, "summary")


if __name__ == "__main__":
    unittest.main()
