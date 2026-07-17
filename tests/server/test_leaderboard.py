#!/usr/bin/env python3
import os
import tempfile
import unittest

# server/ is placed on sys.path by tests/server/conftest.py
from leaderboard import SqliteLeaderboard

_ENTRY_A = {
    "timestamp": "2026-06-16T10:00:00+00:00",
    "room_id": 1,
    "score": 50.0,
    "commands_passed": 8,
    "commands_failed": 2,
    "num_badges": 3,
    "total_modules": 6,
    "badges": {"abc": ["GPS", "BLING"], "def": ["GPS"], "ghi": ["BLING"]},
    "module_counts": {"GPS": 2, "BLING": 2},
    "module_scores": {"GPS": {"passed": 5, "failed": 1}, "BLING": {"passed": 3, "failed": 1}},
    "badge_scores": {"abc": {"passed": 5, "failed": 1}, "def": {"passed": 3, "failed": 1}, "ghi": {"passed": 0, "failed": 0}},
}

_ENTRY_B = {
    "timestamp": "2026-06-16T11:00:00+00:00",
    "room_id": 2,
    "score": 20.0,
    "commands_passed": 3,
    "commands_failed": 7,
    "num_badges": 2,
    "total_modules": 4,
    "badges": {"xyz": ["GPS"]},
    "module_counts": {"GPS": 1},
    "module_scores": {"GPS": {"passed": 3, "failed": 7}},
    "badge_scores": {"xyz": {"passed": 3, "failed": 7}},
}


def _make_lb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return SqliteLeaderboard(path), path


class TestRankOfScore(unittest.TestCase):
    def setUp(self):
        self.lb, self.path = _make_lb()

    def tearDown(self):
        os.unlink(self.path)

    def _record(self, score, timestamp):
        entry = dict(_ENTRY_A, score=score, timestamp=timestamp)
        self.lb.record(entry)

    def _now_iso(self, hours_ago=0):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()

    def test_rank_counts_only_strictly_better_games(self):
        self._record(80.0, self._now_iso(1))
        self._record(50.0, self._now_iso(2))
        self._record(50.0, self._now_iso(3))
        self.assertEqual(self.lb.rank_of_score(50.0), (2, 3))
        self.assertEqual(self.lb.rank_of_score(80.0), (1, 3))

    def test_rank_ignores_games_older_than_24h(self):
        self._record(999.0, self._now_iso(30))  # yesterday's high score
        self._record(50.0, self._now_iso(1))
        self.assertEqual(self.lb.rank_of_score(50.0), (1, 1))


class TestSqliteLeaderboardStats(unittest.TestCase):
    def setUp(self):
        self.lb, self.path = _make_lb()

    def tearDown(self):
        os.unlink(self.path)

    def test_stats_empty_returns_zero_games(self):
        result = self.lb.stats()
        self.assertEqual(result["total_games"], 0)

    def test_stats_single_game_total(self):
        self.lb.record(_ENTRY_A)
        result = self.lb.stats()
        self.assertEqual(result["total_games"], 1)

    def test_stats_avg_score(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        self.assertAlmostEqual(result["avg_score"], 35.0, places=1)

    def test_stats_score_by_team_size(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        self.assertIn("3", result["score_by_team_size"])
        self.assertIn("2", result["score_by_team_size"])
        self.assertAlmostEqual(result["score_by_team_size"]["3"], 50.0, places=1)
        self.assertAlmostEqual(result["score_by_team_size"]["2"], 20.0, places=1)

    def test_stats_module_aggregation(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        gps = result["module_stats"]["GPS"]
        self.assertEqual(gps["passed"], 8)   # 5 + 3
        self.assertEqual(gps["failed"], 8)   # 1 + 7
        self.assertAlmostEqual(gps["success_rate"], 0.5, places=3)

    def test_stats_best_worst_hexpansion(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        # BLING: 3/4 = 0.75, GPS: 8/16 = 0.5
        self.assertEqual(result["best_hexpansion"], "BLING")
        self.assertEqual(result["worst_hexpansion"], "GPS")

    def test_stats_total_commands_issued(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        # A: 8+2=10, B: 3+7=10 → 20 total
        self.assertEqual(result["total_commands_issued"], 20)

    def test_stats_busiest_hexpansion(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        # GPS: 5+1+3+7=16, BLING: 3+1=4 → GPS is busiest
        self.assertEqual(result["busiest_hexpansion"], "GPS")

    def test_stats_distinct_badges_seen(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        # abc, def, ghi, xyz — 4 distinct badge_ids in game_badge_scores
        self.assertEqual(result["distinct_badges_seen"], 4)

    def test_worst_hexpansion_picks_zero_success_module(self):
        # A module that never passes has success_rate 0.0, which is falsy — it
        # must still rank as the worst, not be skipped for a better module.
        entry = dict(
            _ENTRY_A,
            module_scores={"MegaDrive": {"passed": 0, "failed": 1}, "Tildagon2024": {"passed": 4, "failed": 1}},
            badge_scores={},
        )
        self.lb.record(entry)
        result = self.lb.stats()
        self.assertEqual(result["module_stats"]["MegaDrive"]["success_rate"], 0.0)
        self.assertEqual(result["worst_hexpansion"], "MegaDrive")
        self.assertEqual(result["best_hexpansion"], "Tildagon2024")

    def test_stats_no_module_data_omits_module_stats(self):
        entry = dict(_ENTRY_A, module_scores={}, badge_scores={})
        self.lb.record(entry)
        result = self.lb.stats()
        self.assertEqual(result["module_stats"], {})
        self.assertIsNone(result.get("best_hexpansion"))
        self.assertIsNone(result.get("worst_hexpansion"))

    def test_record_without_module_scores_is_safe(self):
        entry = {k: v for k, v in _ENTRY_A.items() if k not in ("module_scores", "badge_scores")}
        self.lb.record(entry)
        result = self.lb.stats()
        self.assertEqual(result["total_games"], 1)
        self.assertEqual(result["module_stats"], {})


class TestDeleteGame(unittest.TestCase):
    def setUp(self):
        self.lb, self.path = _make_lb()

    def tearDown(self):
        os.unlink(self.path)

    def _delete_first(self):
        [entry] = [e for e in self.lb.entries() if e["room_id"] == 1]
        return self.lb.delete(entry["id"])

    def test_delete_removes_entry_from_history(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        self.assertTrue(self._delete_first())
        rooms = {e["room_id"] for e in self.lb.entries()}
        self.assertEqual(rooms, {2})

    def test_delete_returns_false_for_unknown_id(self):
        self.lb.record(_ENTRY_A)
        self.assertFalse(self.lb.delete(9999))
        self.assertEqual(self.lb.stats()["total_games"], 1)

    def test_delete_excludes_game_from_overall_stats(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        self._delete_first()
        result = self.lb.stats()
        # Only ENTRY_B remains: 1 game, its score, its single badge.
        self.assertEqual(result["total_games"], 1)
        self.assertAlmostEqual(result["avg_score"], 20.0, places=1)
        self.assertEqual(result["distinct_badges_seen"], 1)

    def test_delete_excludes_game_from_hexpansion_stats(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        self._delete_first()
        gps = self.lb.stats()["module_stats"]["GPS"]
        # ENTRY_A's GPS 5/1 is gone; only ENTRY_B's GPS 3/7 remains.
        self.assertEqual(gps["passed"], 3)
        self.assertEqual(gps["failed"], 7)
        # BLING existed only in ENTRY_A, so it disappears entirely.
        self.assertNotIn("BLING", self.lb.stats()["module_stats"])



if __name__ == "__main__":
    unittest.main()
