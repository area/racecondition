import json
from pathlib import Path

LEADERBOARD_PATH = Path(__file__).resolve().parent / "leaderboard.json"


class FilesystemLeaderboard:
    def __init__(self, path=None):
        self._path = path or LEADERBOARD_PATH

    def _load(self):
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text())
        return data if isinstance(data, list) else []

    def record(self, entry):
        entries = self._load()
        entries.append(entry)
        entries.sort(key=lambda e: (e["score"], e.get("num_badges", 0), -e.get("commands_failed", 0)), reverse=True)
        self._path.write_text(json.dumps(entries, indent=2))

    def entries(self):
        return self._load()


class InMemoryLeaderboard:
    def __init__(self):
        self._entries = []

    def record(self, entry):
        self._entries.append(entry)
        self._entries.sort(key=lambda e: e["score"], reverse=True)

    def entries(self):
        return list(self._entries)
