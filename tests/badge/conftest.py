"""
Root conftest — stubs MicroPython / Tildagon-only modules so the badge package
is importable on desktop Python for testing.

Strategy: pre-register a stub for sys.modules['badge'] with __path__ pointing
at the badge/ directory, where the app modules live (badge/app.py,
badge/session.py, …). This lets Python find every badge.* submodule
(badge.app, badge.session, …), mirroring how the firmware imports the
published subpackage as `apps.<name>.badge.*`. The Tildagon framework's own
top-level `app` module (badge/app.py does `import app` for the App base class)
is stubbed separately. All hardware modules (machine, imu, …) are replaced with
MagicMocks, and the two MicroPython-only time functions are shim-added to
the standard library time module.
"""

import sys
import types
import time
import hashlib
import binascii
from pathlib import Path
from unittest.mock import MagicMock

# ── MicroPython stdlib aliases ──────────────────────────────────────────────
# uhashlib / ubinascii are the MicroPython names for the CPython stdlib
# modules; their APIs match closely enough for the app's use.
sys.modules.setdefault("uhashlib", hashlib)
sys.modules.setdefault("ubinascii", binascii)

# ── MicroPython time shims ──────────────────────────────────────────────────
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.time() * 1000)
if not hasattr(time, "ticks_diff"):
    time.ticks_diff = lambda a, b: a - b

# ── Hardware module stubs ────────────────────────────────────────────────────
_uart_stub = MagicMock()
_uart_stub.any.return_value = 0          # no UART data available by default

_machine = MagicMock()
_machine.UART.return_value = _uart_stub

# Default to a supported OS version; tests that exercise the v1 gate override
# _ota.get_version.return_value to a "v1.*" string.
_ota = MagicMock()
_ota.get_version.return_value = "v2.0.0"

# The badge `settings` module is a key/default store; return the caller's
# default so brightness math (settings.get("pattern_brightness", 0.1)) works.
_settings = MagicMock()
_settings.get.side_effect = lambda key, default=None: default

# events.input is stubbed with a faithful mini-implementation rather than a
# MagicMock: is_cancel() depends on Button's ancestry semantics (__contains__
# walking the parents chain), and a MagicMock stub is exactly how the
# parent -> parents firmware change (tildagonOS 8aa7bd8) slipped past the
# suite. Mirrors modules/events/input.py at firmware HEAD; __slots__ omitted
# because MicroPython ignores it anyway. test_buttons.py diffs this stub
# against the real firmware source when a checkout is available.

class _Button:
    def __init__(self, name, group, parent=None):
        self.name = name
        self.group = group
        if isinstance(parent, _Button):
            self.parents = [parent]
        elif parent is None:
            self.parents = []
        else:
            self.parents = parent
        self._all_parents = None

    def __hash__(self):
        return hash((self.name, self.group))

    def __repr__(self):
        return "Button({}.{})".format(self.group, self.name)

    def __eq__(self, other):
        return self.name == other.name and self.group == other.group

    @property
    def all_parents(self):
        if self._all_parents is None:
            self._all_parents = []
            for parent in self.parents:
                self._all_parents.append(parent)
                self._all_parents += parent.all_parents
        return self._all_parents

    def __contains__(self, other):
        if other == self:
            return True
        for parent in self.all_parents:
            if other == parent:
                return True
        return False

    def find_parent_in_group(self, group):
        if self.group == group:
            return self
        for parent in self.all_parents:
            if parent.group == group:
                return parent
        return None


_BUTTON_TYPES = {
    name: _Button(name, "System")
    for name in ("UNDEFINED", "CANCEL", "CONFIRM", "UP", "DOWN", "LEFT", "RIGHT")
}


class _Buttons:
    """State-tracking half of firmware Buttons, minus the eventbus wiring."""
    def __init__(self, app):
        self.buttons = {}

    def get(self, button, default=None):
        matching = [v for (b, v) in self.buttons.items() if b == button or button in b]
        return any(matching)

    def __getitem__(self, item):
        return self.buttons[item]

    def clear(self):
        self.buttons.clear()


_events_input = types.ModuleType("events.input")
_events_input.Button = _Button
_events_input.BUTTON_TYPES = _BUTTON_TYPES
_events_input.Buttons = _Buttons
_events_input.ButtonDownEvent = type("ButtonDownEvent", (), {})
_events_input.ButtonUpEvent = type("ButtonUpEvent", (), {})

# The real `events` module exposes an Event base class; the differential test
# in test_buttons.py execs the genuine firmware input.py, which subclasses it,
# so the stub needs a real class rather than a MagicMock attribute.
_events = MagicMock()
_events.Event = type("Event", (), {})

# app_components is firmware-only, but decorate() reads symbols["arrows"] from it,
# so give the mock the real arrow glyphs (mirrors app_components/tokens.py).
_app_components = MagicMock()
_app_components.symbols = {
    "arrows": {
        "left": "←",
        "up": "↑",
        "right": "→",
        "down": "↓",
        "left_right": "↔",
        "up_down": "↕",
        "north_west": "↖",
        "north_east": "↗",
        "south_east": "↘",
        "south_west": "↙",
    },
}

# Board detection: tests exercise the 2024 module, so report a 2024-family PID.
_frontboards_utils = MagicMock()
_frontboards_utils.detect_frontboard.return_value = 0x2400

for _name, _stub in [
    ("machine",                   _machine),
    ("ota",                       _ota),
    ("settings",                  _settings),
    ("imu",                       MagicMock()),
    ("tildagonos",                MagicMock()),
    ("app_components",            _app_components),
    ("frontboards",               MagicMock()),
    ("frontboards.utils",         _frontboards_utils),
    ("events",                    _events),
    ("events.input",              _events_input),
    ("system",                    MagicMock()),
    ("system.eventbus",           MagicMock()),
    ("system.hexpansion",         MagicMock()),
    ("system.hexpansion.events",  MagicMock()),
    ("system.hexpansion.util",    MagicMock()),
    ("system.patterndisplay",     MagicMock()),
    ("system.patterndisplay.events", MagicMock()),
]:
    sys.modules.setdefault(_name, _stub)

# ── badge package stub ───────────────────────────────────────────────────────
# Pre-register sys.modules['badge'] as a lightweight package stub whose __path__
# points at the badge/ directory.  Python's import system will find every
# badge.* submodule (badge.app, badge.session, …) there, mirroring how the
# firmware imports the published subpackage as `apps.<name>.badge.*`.

_badge_stub = types.ModuleType("badge")
# conftest lives at tests/badge/; the real badge package is at the repo root.
_badge_stub.__path__ = [str(Path(__file__).resolve().parents[2] / "badge")]
_badge_stub.__package__ = "badge"
sys.modules["badge"] = _badge_stub

# ── Tildagon framework `app` module stub ─────────────────────────────────────
# badge/app.py does `import app; class RaceConditionApp(app.App)`.  Provide a
# no-op App base class so that resolves without the real framework.

class _App:
    """No-op stand-in for the Tildagon framework's app.App."""
    def __init__(self, *args, **kwargs): pass

_app_stub = types.ModuleType("app")
_app_stub.App = _App
sys.modules["app"] = _app_stub
