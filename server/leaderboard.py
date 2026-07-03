import json
import threading
from datetime import datetime, timedelta, timezone

from db import open_db


class SqliteLeaderboard:
    def __init__(self, path=None):
        self._conn = open_db(path)
        self._lock = threading.Lock()

    def record(self, entry):
        with self._lock:
            cur = self._conn.execute(
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
            entry_id = cur.lastrowid
            for module, counts in entry.get("module_scores", {}).items():
                self._conn.execute(
                    "INSERT INTO game_module_results (entry_id, module, passed, failed) VALUES (?, ?, ?, ?)",
                    (entry_id, module, counts.get("passed", 0), counts.get("failed", 0)),
                )
            for badge_id, counts in entry.get("badge_scores", {}).items():
                self._conn.execute(
                    "INSERT INTO game_badge_scores (entry_id, badge_id, passed, failed) VALUES (?, ?, ?, ?)",
                    (entry_id, badge_id, counts.get("passed", 0), counts.get("failed", 0)),
                )
            self._conn.commit()

    def rank_of_score(self, score):
        """(rank, total_games) for a just-recorded score, last 24 hours.

        Ties share a rank: rank = 1 + number of strictly better games.
        Timestamps are stored as ISO-8601 UTC, so lexicographic comparison
        against an ISO cutoff is chronologically correct.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self._lock:
            higher = self._conn.execute(
                "SELECT COUNT(*) FROM leaderboard_entries WHERE score > ? AND timestamp >= ?",
                (score, cutoff),
            ).fetchone()[0]
            total = self._conn.execute(
                "SELECT COUNT(*) FROM leaderboard_entries WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]
        return higher + 1, total

    def entries(self):
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, timestamp, room_id, score, commands_passed, commands_failed,
                          num_badges, total_modules, badges, module_counts
                   FROM leaderboard_entries
                   ORDER BY score DESC, num_badges DESC, commands_failed ASC"""
            ).fetchall()

            badge_scores = {}
            module_results = {}
            if rows:
                ids = [r[0] for r in rows]
                ph = ",".join("?" * len(ids))
                for eid, bid, p, f in self._conn.execute(
                    f"SELECT entry_id, badge_id, passed, failed FROM game_badge_scores WHERE entry_id IN ({ph})", ids
                ).fetchall():
                    badge_scores.setdefault(eid, {})[bid] = {"passed": p, "failed": f}
                for eid, mod, p, f in self._conn.execute(
                    f"SELECT entry_id, module, passed, failed FROM game_module_results WHERE entry_id IN ({ph})", ids
                ).fetchall():
                    module_results.setdefault(eid, {})[mod] = {"passed": p, "failed": f}

        return [
            {
                "timestamp": row[1],
                "room_id": row[2],
                "score": row[3],
                "commands_passed": row[4],
                "commands_failed": row[5],
                "num_badges": row[6],
                "total_modules": row[7],
                "badges": json.loads(row[8]),
                "module_counts": json.loads(row[9]),
                "badge_scores": badge_scores.get(row[0], {}),
                "module_results": module_results.get(row[0], {}),
            }
            for row in rows
        ]

    def stats(self):
        with self._lock:
            agg = self._conn.execute(
                """SELECT
                       COUNT(*) AS total_games,
                       AVG(score) AS avg_score,
                       AVG(num_badges) AS avg_team_size,
                       AVG(CAST(total_modules AS REAL) / num_badges) AS avg_modules_per_badge,
                       MAX(total_modules) AS max_modules_in_game,
                       AVG(commands_passed * 1.0 / (commands_passed + commands_failed)) AS avg_pass_rate,
                       SUM(commands_passed) AS total_commands_passed,
                       SUM(commands_failed) AS total_commands_failed
                   FROM leaderboard_entries
                   WHERE commands_passed + commands_failed > 0"""
            ).fetchone()

            total_games = self._conn.execute("SELECT COUNT(*) FROM leaderboard_entries").fetchone()[0]

            score_by_size = self._conn.execute(
                "SELECT num_badges, AVG(score) FROM leaderboard_entries GROUP BY num_badges ORDER BY num_badges"
            ).fetchall()

            module_rows = self._conn.execute(
                """SELECT module, SUM(passed), SUM(failed)
                   FROM game_module_results
                   GROUP BY module
                   ORDER BY module"""
            ).fetchall()

            distinct_badges = self._conn.execute(
                "SELECT COUNT(DISTINCT badge_id) FROM game_badge_scores"
            ).fetchone()[0]

        if total_games == 0:
            return {"total_games": 0}

        module_stats = {}
        for module, passed, failed in module_rows:
            total = passed + failed
            module_stats[module] = {
                "passed": passed,
                "failed": failed,
                "success_rate": round(passed / total, 3) if total else None,
            }

        best = max(module_stats, key=lambda m: module_stats[m]["success_rate"] or 0) if module_stats else None
        worst = min(module_stats, key=lambda m: module_stats[m]["success_rate"] or 1) if module_stats else None
        busiest = max(module_stats, key=lambda m: module_stats[m]["passed"] + module_stats[m]["failed"]) if module_stats else None

        total_commands_passed = agg[6] or 0
        total_commands_failed = agg[7] or 0

        return {
            "total_games": total_games,
            "avg_score": round(agg[1], 2) if agg[1] is not None else None,
            "avg_team_size": round(agg[2], 2) if agg[2] is not None else None,
            "score_by_team_size": {str(r[0]): round(r[1], 2) for r in score_by_size},
            "avg_modules_per_badge": round(agg[3], 2) if agg[3] is not None else None,
            "max_modules_in_game": agg[4],
            "avg_pass_rate": round(agg[5], 3) if agg[5] is not None else None,
            "total_commands_issued": total_commands_passed + total_commands_failed,
            "distinct_badges_seen": distinct_badges,
            "module_stats": module_stats,
            "best_hexpansion": best,
            "worst_hexpansion": worst,
            "busiest_hexpansion": busiest,
        }


