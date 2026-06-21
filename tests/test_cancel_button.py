import time
import unittest
from unittest.mock import MagicMock, patch

import app.app as _app_module
from app.app import RaceConditionApp, CANCEL_HOLD_MS


def _make_cancel_event():
    """A ButtonDownEvent/ButtonUpEvent whose button is the cancel button."""
    button = MagicMock()
    button.name = "cancel"
    button.parent = None
    event = MagicMock()
    event.button = button
    return event


def _make_app():
    room_client = MagicMock()
    room_client.poll.return_value = {
        "room_state": "in-round",
        "time_remaining_s": 60.0,
        "assignment": None,
        "display": None,
        "scores": {"passed": 0, "failed": 0},
        "badge_scores": {},
        "badge_count": 1,
        "colour": "red",
    }
    with patch.object(RaceConditionApp, "_scan"):
        return RaceConditionApp(room_client=room_client)


def _put_in_round(app, module=None):
    app.session.start_room(1)
    app.session.set_room_state("in-round")
    if module is not None:
        app.session.expected_module = module


class TestCancelShortPress(unittest.TestCase):

    def test_short_press_in_round_fires_command_on_release(self):
        module = MagicMock()
        a = _make_app()
        _put_in_round(a, module)

        down = _make_cancel_event()
        a._on_button_down(down)
        a._on_button_up(down)

        module.on_button_down.assert_called_once_with(down)

    def test_hold_does_not_fire_command(self):
        module = MagicMock()
        a = _make_app()
        _put_in_round(a, module)

        down = _make_cancel_event()
        a._on_button_down(down)
        # backdate hold_start so elapsed >= threshold
        a.session.cancel_hold_start = time.ticks_ms() - CANCEL_HOLD_MS
        a._on_button_up(down)

        module.on_button_down.assert_not_called()

    def test_cancel_when_not_in_round_does_not_fire_command(self):
        module = MagicMock()
        a = _make_app()
        a.session.start_room(1)
        # left in "waiting" state — expected_module set separately to confirm
        # it's the room_state check that gates the command, not module presence
        a.session.expected_module = module

        down = _make_cancel_event()
        a._on_button_down(down)
        a._on_button_up(down)

        module.on_button_down.assert_not_called()

    def test_release_after_update_triggered_leave_does_not_fire(self):
        """update() calls stop_room() which clears cancel_hold_start;
        a subsequent button_up should not fire the command."""
        module = MagicMock()
        a = _make_app()
        _put_in_round(a, module)

        down = _make_cancel_event()
        a._on_button_down(down)
        # simulate stop_room() clearing the hold tracker (as update() would do)
        a.session.cancel_hold_start = None
        a._on_button_up(down)

        module.on_button_down.assert_not_called()


if __name__ == "__main__":
    unittest.main()
