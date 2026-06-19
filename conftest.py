"""
Root conftest — stubs MicroPython / Tildagon-only modules so the app package
is importable on desktop Python for testing.

Strategy: pre-register a stub for sys.modules['app'] with __path__ pointing
at the real app/ directory.  This lets Python find every app.* submodule
normally while never executing app/__init__.py (which would trigger the
`import app` circular reference in app/app.py) or any Tildagon framework
imports.  All hardware modules (machine, imu, …) are replaced with
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

for _name, _stub in [
    ("machine",                   _machine),
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

# ── app package stub ─────────────────────────────────────────────────────────
# Pre-register sys.modules['app'] as a lightweight module stub whose __path__
# points at the real app/ directory.  Python's import system will find all
# app.* submodules there without ever running app/__init__.py.
# The stub also exposes a no-op App base class so that app/app.py's
# `class TildateamApp(app.App)` resolves without error.

class _App:
    """No-op stand-in for the Tildagon framework's app.App."""
    def __init__(self, *args, **kwargs): pass

_app_stub = types.ModuleType("app")
_app_stub.__path__ = [str(Path(__file__).parent / "app")]
_app_stub.__package__ = "app"
_app_stub.App = _App

sys.modules["app"] = _app_stub
