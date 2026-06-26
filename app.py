"""Badge app entry point.

The Tildagon installer requires an `app.py` at the tarball root, and the
launcher imports `apps.<owner>_<title>.app` and reads `__app_export__` off it.
The actual app code lives in the `badge/` subpackage; this shim just re-exports
the app class so the launcher finds it.
"""

from .badge.app import RaceConditionApp

__app_export__ = RaceConditionApp
