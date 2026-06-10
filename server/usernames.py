import threading

from db import open_db
from names import generate_name


class UserRegistry:
    def __init__(self, path=None):
        self._conn = open_db(path)
        self._lock = threading.Lock()

    def set(self, badge_id, username):
        username = username.strip()[:16]
        if not username:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO usernames (badge_id, username) VALUES (?, ?)"
                " ON CONFLICT(badge_id) DO UPDATE SET username = excluded.username",
                (badge_id, username),
            )
            self._conn.commit()

    def get(self, badge_id):
        row = self._conn.execute(
            "SELECT username FROM usernames WHERE badge_id = ?", (badge_id,)
        ).fetchone()
        return row[0] if row else generate_name(badge_id)

    def delete(self, badge_id):
        with self._lock:
            self._conn.execute(
                "DELETE FROM usernames WHERE badge_id = ?", (badge_id,)
            )
            self._conn.commit()

    def all(self):
        rows = self._conn.execute(
            "SELECT badge_id, username FROM usernames"
        ).fetchall()
        return {row[0]: row[1] for row in rows}
