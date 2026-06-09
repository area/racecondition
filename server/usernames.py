import json
from pathlib import Path

from names import generate_name

USERNAMES_PATH = Path(__file__).resolve().parent / "usernames.json"


class UserRegistry:
    def __init__(self, path=None):
        self._path = path or USERNAMES_PATH
        self._data = json.loads(self._path.read_text()) if self._path.exists() else {}

    def set(self, badge_id, username):
        username = username.strip()[:16]
        if not username:
            return
        self._data[badge_id] = username
        self._path.write_text(json.dumps(self._data, indent=2))

    def get(self, badge_id):
        return self._data.get(badge_id) or generate_name(badge_id)

    def delete(self, badge_id):
        if badge_id in self._data:
            del self._data[badge_id]
            self._path.write_text(json.dumps(self._data, indent=2))

    def all(self):
        return dict(self._data)
