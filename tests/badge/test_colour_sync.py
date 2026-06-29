import os
import sys

from badge.constants import COLOURS as APP_COLOURS

# server/ uses flat imports, so add it to the path to load room.py directly
# (the server can't import the badge modules, hence the duplicated list this test guards).
_SERVER = os.path.join(os.path.dirname(__file__), "..", "..", "server")
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from room import COLOURS as SERVER_COLOURS  # noqa: E402


def test_badge_and_server_colours_match():
    # The badge maps these names to RGB (badge/constants.py) and the server
    # assigns them in order (server/room.py). They live in two runtimes that
    # can't share a module, so this is the only thing keeping them honest.
    assert SERVER_COLOURS == APP_COLOURS
