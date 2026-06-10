import json
import threading

from db import open_db


class SqliteLeaderboard:
    def __init__(self, path=None):
        self._conn = open_db(path)
        self._lock = threading.Lock()

    def record(self, entry):
        with self._lock:
            self._conn.execute(
                """INSERT INTO leaderboard_entries
                   (timestamp, room_id, score, commands_passed, commands_failed,
                    num_badges, total_modules, badges, module_counts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["timestamp"],
                    entry["room_id"],
                    entry["score"],
                    entry["commands_passed"],
                    entry["commands_failed"],
                    entry["num_badges"],
                    entry["total_modules"],
                    json.dumps(entry.get("badges", {})),
                    json.dumps(entry.get("module_counts", {})),
                ),
            )
            self._conn.commit()

    def entries(self):
        rows = self._conn.execute(
            """SELECT timestamp, room_id, score, commands_passed, commands_failed,
                      num_badges, total_modules, badges, module_counts
               FROM leaderboard_entries
               ORDER BY score DESC, num_badges DESC, commands_failed ASC"""
        ).fetchall()
        return [
            {
                "timestamp": row[0],
                "room_id": row[1],
                "score": row[2],
                "commands_passed": row[3],
                "commands_failed": row[4],
                "num_badges": row[5],
                "total_modules": row[6],
                "badges": json.loads(row[7]),
                "module_counts": json.loads(row[8]),
            }
            for row in rows
        ]


class InMemoryLeaderboard:
    def __init__(self):
        self._entries = []

    def record(self, entry):
        self._entries.append(entry)
        self._entries.sort(key=lambda e: e["score"], reverse=True)

    def entries(self):
        return list(self._entries)
