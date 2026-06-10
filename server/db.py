import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "tildateam.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usernames (
    badge_id TEXT PRIMARY KEY,
    username TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leaderboard_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    room_id INTEGER NOT NULL,
    score REAL NOT NULL,
    commands_passed INTEGER NOT NULL,
    commands_failed INTEGER NOT NULL,
    num_badges INTEGER NOT NULL,
    total_modules INTEGER NOT NULL,
    badges TEXT NOT NULL,
    module_counts TEXT NOT NULL
);
"""


def open_db(path=None):
    conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn
