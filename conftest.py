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

for _name, _stub in [
    ("machine",                   _machine),
    ("ota",                       _ota),
    ("imu",                       MagicMock()),
    ("tildagonos",                MagicMock()),
    ("app_components",            MagicMock()),
    ("events",                    MagicMock()),
    ("events.input",              MagicMock()),
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
_badge_stub.__path__ = [str(Path(__file__).parent / "badge")]
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
