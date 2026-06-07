import time

from .hexpansion.base import CommandStatus

TEST_SKIP_HOLD_MS = 2000


def _is_cancel(event):
    button = getattr(event.button, "parent", None) or event.button
    return button.name.lower() == "cancel"


class TestSession:
    def __init__(self, modules):
        self._items = [(m, cmd) for m in modules for cmd in m.get_capabilities()["commands"]]
        self._index = 0
        self._passed = 0
        self._skipped = 0
        self._cancel_hold_start = None
        self._cancel_down_event = None
        self.state = "command" if self._items else "done"
        if self._items:
            self._items[0][0].set_command(self._items[0][1])

    @property
    def current_module(self):
        return self._items[self._index][0] if self.state == "command" else None

    @property
    def current_command(self):
        return self._items[self._index][1] if self.state == "command" else None

    @property
    def total(self):
        return len(self._items)

    @property
    def index(self):
        return self._index

    @property
    def passed(self):
        return self._passed

    @property
    def skipped(self):
        return self._skipped

    def on_button_down(self, event):
        if self.state == "summary":
            self.state = "done"
            return
        if _is_cancel(event):
            if self._cancel_hold_start is None:
                self._cancel_hold_start = time.ticks_ms()
                self._cancel_down_event = event
        elif self.state == "command":
            self._items[self._index][0].on_button_down(event)

    def on_button_up(self, event):
        if not _is_cancel(event) or self.state != "command":
            return
        if self._cancel_hold_start is not None and self._cancel_down_event is not None:
            held = time.ticks_diff(time.ticks_ms(), self._cancel_hold_start)
            if held < TEST_SKIP_HOLD_MS:
                self._items[self._index][0].on_button_down(self._cancel_down_event)
        self._cancel_hold_start = None
        self._cancel_down_event = None

    def update(self):
        if self.state != "command":
            return
        if self._cancel_hold_start is not None:
            held = time.ticks_diff(time.ticks_ms(), self._cancel_hold_start)
            if held >= TEST_SKIP_HOLD_MS:
                self._skipped += 1
                self._cancel_hold_start = None
                self._cancel_down_event = None
                self._advance()
                return
        if self._items[self._index][0].check_command() == CommandStatus.PASSED:
            self._passed += 1
            self._advance()

    def _advance(self):
        self._index += 1
        if self._index >= len(self._items):
            self.state = "summary"
        else:
            m, cmd = self._items[self._index]
            m.set_command(cmd)
