#!/usr/bin/env python3
import os
import tempfile
import unittest
from pathlib import Path
import sys

_scripts_dir = str(Path(__file__).parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from leaderboard import SqliteLeaderboard, InMemoryLeaderboard

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

    def test_stats_distinct_badges_seen(self):
        self.lb.record(_ENTRY_A)
        self.lb.record(_ENTRY_B)
        result = self.lb.stats()
        # abc, def, ghi, xyz — 4 distinct badge_ids in game_badge_scores
        self.assertEqual(result["distinct_badges_seen"], 4)

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


class TestInMemoryLeaderboardStats(unittest.TestCase):
    def test_stats_empty(self):
        lb = InMemoryLeaderboard()
        self.assertEqual(lb.stats()["total_games"], 0)

    def test_stats_aggregates_modules(self):
        lb = InMemoryLeaderboard()
        lb.record(_ENTRY_A)
        lb.record(_ENTRY_B)
        result = lb.stats()
        self.assertEqual(result["total_games"], 2)
        gps = result["module_stats"]["GPS"]
        self.assertEqual(gps["passed"], 8)
        self.assertEqual(gps["failed"], 8)


if __name__ == "__main__":
    unittest.main()
