"""
Server-side test setup.

The server (server/*.py) uses flat imports — `from room import Room`,
`from leaderboard import SqliteLeaderboard`, etc. — so it expects its own
directory on sys.path.  Add it here, once, so every server test can import the
modules directly instead of loading each one via importlib.spec_from_file_location.

Note: these tests run on real CPython with real aiohttp/sqlite; they deliberately
do NOT get the MicroPython hardware stubs from tests/badge/conftest.py, which is
scoped to that directory.
"""

import sys
from pathlib import Path

_SERVER = str(Path(__file__).resolve().parents[2] / "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)
