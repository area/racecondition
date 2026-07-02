import time

from .buttons import is_cancel
from .hexpansion.base import CommandStatus

TEST_SKIP_HOLD_MS = 2000


class TestSession:
    __test__ = False  # not a pytest test class despite the Test* name

    def __init__(self, modules):
        self._modules = list(modules)
        self._items = []
        self._queued = set()
        self._index = 0
        self._passed = 0
        self._skipped = 0
        self._cancel_hold_start = None
        self._cancel_down_event = None
        self._discover()
        if self._items:
            self.state = "command"
            self._start_current()
        elif self._modules:
            # Modules are connected but report no commands yet (e.g. GPS without a
            # fix). Wait and keep re-querying rather than exiting immediately.
            self.state = "waiting"
        else:
            self.state = "done"

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

    def cancel_hold_progress(self, now_ms):
        # Fraction (0..1) of the hold-to-skip/finish gesture completed, or None
        # when the cancel button isn't being held. Drives the on-screen ring.
        if self._cancel_hold_start is None:
            return None
        held = time.ticks_diff(now_ms, self._cancel_hold_start)
        return max(0.0, min(1.0, held / TEST_SKIP_HOLD_MS))

    def on_button_down(self, event):
        if self.state == "summary":
            self.state = "done"
            return
        if is_cancel(event):
            if self._cancel_hold_start is None:
                self._cancel_hold_start = time.ticks_ms()
                self._cancel_down_event = event
            return
        if self.state == "command":
            self._items[self._index][0].on_button_down(event)
        elif self.state == "waiting":
            # Forward input so modules can reveal more commands (e.g. pressing x/y/z
            # on the MegaDrive controller switches it into six-button mode).
            for module in self._modules:
                module.on_button_down(event)

    def on_button_up(self, event):
        if not is_cancel(event):
            return
        if (self.state == "command"
                and self._cancel_hold_start is not None
                and self._cancel_down_event is not None):
            held = time.ticks_diff(time.ticks_ms(), self._cancel_hold_start)
            if held < TEST_SKIP_HOLD_MS:
                self._items[self._index][0].on_button_down(self._cancel_down_event)
        self._cancel_hold_start = None
        self._cancel_down_event = None

    def update(self):
        if self.state in ("summary", "done"):
            return
        self._discover()
        if self.state == "waiting":
            self._update_waiting()
        elif self.state == "command":
            self._update_command()

    def _update_waiting(self):
        if self._index < len(self._items):
            self.state = "command"
            self._start_current()
            return
        # Nothing left to test right now; hold cancel to finish and see the summary.
        if self._cancel_hold_start is not None:
            held = time.ticks_diff(time.ticks_ms(), self._cancel_hold_start)
            if held >= TEST_SKIP_HOLD_MS:
                self.state = "summary"
                self._cancel_hold_start = None
                self._cancel_down_event = None

    def _update_command(self):
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

    def _discover(self):
        # Append any newly-revealed commands to the live queue, keeping order and
        # never re-adding ones we have already seen.
        for module in self._modules:
            for command in module.get_capabilities()["commands"]:
                key = (module, command)
                if key not in self._queued:
                    self._queued.add(key)
                    self._items.append((module, command))

    def _start_current(self):
        module, command = self._items[self._index]
        module.set_command(command)

    def _advance(self):
        self._index += 1
        if self._index < len(self._items):
            self._start_current()
        elif self._modules:
            # Queue drained for now, but connected modules may still reveal more.
            self.state = "waiting"
        else:
            self.state = "summary"
