import json
import math
import random
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

STALE_BADGE_SECONDS = 20
ROUND_DURATION_S = 120
COLOURS = ["red", "green", "blue", "yellow", "purple", "orange"]

LEADERBOARD_PATH = Path(__file__).resolve().parent / "leaderboard.json"


class Room:
    def __init__(self, room_id):
        self.room_id = room_id
        self._lock = Lock()
        self._reset_state()

    # ------------------------------------------------------------------ public

    def join(self, badge_id, capabilities):
        with self._lock:
            self._prune_stale()
            self._set_badge(badge_id, capabilities)
            return self._poll_response(badge_id)

    def poll(self, badge_id, capabilities, result=None, session_token=None):
        with self._lock:
            self._prune_stale()
            self._set_badge(badge_id, capabilities)
            self._check_expiry()
            if result is not None and self._state == "in-round":
                if session_token == self._session_tokens.get(badge_id):
                    self._apply_result(badge_id, result)
            return self._poll_response(badge_id)

    def leave(self, badge_id):
        with self._lock:
            self._prune_stale()
            if self._state == "finished":
                self._dismissed.add(badge_id)
            self._badges.pop(badge_id, None)
            self._assignments.pop(badge_id, None)
            self._colours.pop(badge_id, None)
            self._badge_scores.pop(badge_id, None)
            self._session_tokens.pop(badge_id, None)
            if not self._badges:
                self._reset_state()
            elif self._state == "finished":
                self._check_all_dismissed()
            badge_count = len(self._badges)
        return {"room_id": self.room_id, "status": "left", "badge_count": badge_count}

    def start_round(self, badge_id):
        with self._lock:
            self._prune_stale()
            if self._state != "waiting":
                return {"room_id": self.room_id, "error": "Round already in progress"}
            if badge_id not in self._badges:
                return {"room_id": self.room_id, "error": "Badge not in room"}
            self._state = "in-round"
            self._round_started_at = self._now()
            self._scores = {"passed": 0, "failed": 0}
            self._badge_scores = {bid: {"passed": 0, "failed": 0} for bid in self._badges}
            self._assignments = {}
        return {"room_id": self.room_id, "status": "started", "room_state": "in-round"}

    def set_timer(self, seconds):
        with self._lock:
            if self._state != "in-round" or self._round_started_at is None:
                return {"room_id": self.room_id, "error": "Room not in-round"}
            self._round_started_at = self._now() - (ROUND_DURATION_S - seconds)
            return {"room_id": self.room_id, "status": "ok", "time_remaining_s": float(seconds)}

    def dismiss_score(self, badge_id):
        with self._lock:
            if self._state == "finished":
                self._dismissed.add(badge_id)
                self._check_all_dismissed()
            return {"room_id": self.room_id, "status": "ok", "room_state": self._state}

    def admin_snapshot(self):
        with self._lock:
            self._prune_stale()
            badges = [
                {
                    "badge_id": bid,
                    "colour": self._colours.get(bid),
                    "module_count": len(badge["capabilities"]),
                    "last_seen_s": round(self._now() - badge["last_seen"], 1),
                }
                for bid, badge in self._badges.items()
            ]
            assignments = [
                {
                    "id": a["id"],
                    "target_badge_id": tid,
                    "target_colour": self._colours.get(tid),
                    "module": a["module"],
                    "command": a["command"],
                    "age_s": round(self._now() - a["issued_at"], 1),
                }
                for tid, a in self._assignments.items()
            ]
            return {
                "room_id": self.room_id,
                "room_state": self._state,
                "badge_count": len(self._badges),
                "scores": self._scores,
                "badges": badges,
                "assignments": assignments,
            }

    # ----------------------------------------------------------------- private

    def _reset_state(self):
        self._badges = {}
        self._assignments = {}
        self._colours = {}
        self._badge_scores = {}
        self._session_tokens = {}
        self._next_assignment_id = 1
        self._scores = {"passed": 0, "failed": 0}
        self._state = "waiting"
        self._round_started_at = None
        self._dismissed = set()

    def _now(self):
        return time.monotonic()

    def _prune_stale(self):
        cutoff = self._now() - STALE_BADGE_SECONDS
        stale = [bid for bid, b in self._badges.items() if b["last_seen"] < cutoff]
        for bid in stale:
            self._badges.pop(bid, None)
            self._assignments.pop(bid, None)
            self._colours.pop(bid, None)
            self._badge_scores.pop(bid, None)
            self._session_tokens.pop(bid, None)
            self._dismissed.discard(bid)
        if not self._badges:
            self._reset_state()
        elif stale and self._state == "finished":
            self._check_all_dismissed()

    def _set_badge(self, badge_id, capabilities):
        if badge_id not in self._colours:
            used = set(self._colours.values())
            self._colours[badge_id] = next((c for c in COLOURS if c not in used), COLOURS[0])
        if badge_id not in self._badge_scores:
            self._badge_scores[badge_id] = {"passed": 0, "failed": 0}
        if badge_id not in self._session_tokens:
            self._session_tokens[badge_id] = secrets.token_hex(16)
        self._badges[badge_id] = {"capabilities": capabilities, "last_seen": self._now()}

    def _command_pool(self):
        return [
            (module, command)
            for badge in self._badges.values()
            for module, commands in badge["capabilities"].items()
            for command in commands
        ]

    def _badge_can_run(self, badge_id, module, command):
        badge = self._badges.get(badge_id)
        return badge is not None and command in badge["capabilities"].get(module, ())

    def _assignment_for(self, badge_id):
        existing = self._assignments.get(badge_id)
        if existing:
            return existing
        if badge_id not in self._badges:
            return None
        candidates = [(m, c) for m, c in self._command_pool() if self._badge_can_run(badge_id, m, c)]
        if not candidates:
            return None
        module, command = random.choice(candidates)
        assignment_id = "{}-{}".format(id(self), self._next_assignment_id)
        self._next_assignment_id += 1
        assignment = {"id": assignment_id, "target_badge_id": badge_id,
                      "module": module, "command": command, "issued_at": self._now()}
        self._assignments[badge_id] = assignment
        return assignment

    def _select_instruction(self, badge_id):
        all_assignments = list(self._assignments.items())
        if not all_assignments:
            return None
        other = [(tid, a) for tid, a in all_assignments if tid != badge_id]
        target_id, assignment = random.choice(other if other else all_assignments)
        return {
            "module": assignment["module"],
            "command": assignment["command"],
            "target_colour": self._colours.get(target_id),
        }

    def _apply_result(self, badge_id, result):
        if not isinstance(result, dict):
            return
        expected = self._assignments.get(badge_id)
        if not expected or result.get("assignment_id") != expected["id"]:
            return
        status = result.get("status")
        if status not in ("passed", "failed"):
            return
        self._scores[status] += 1
        self._badge_scores.setdefault(badge_id, {"passed": 0, "failed": 0})[status] += 1
        self._assignments.pop(badge_id, None)

    def _check_expiry(self):
        if self._state != "in-round" or self._round_started_at is None:
            return
        if self._now() - self._round_started_at >= ROUND_DURATION_S:
            self._state = "finished"
            self._dismissed = set()
            self._record_score()

    def _calculate_score(self):
        num_badges = len(self._badges)
        if num_badges == 0:
            return 0.0
        total_modules = sum(len(b["capabilities"]) for b in self._badges.values())
        commands_passed = self._scores["passed"]
        avg_modules = total_modules / num_badges
        return round(commands_passed * math.sqrt(num_badges) * avg_modules, 2)

    def _record_score(self):
        num_badges = len(self._badges)
        total_modules = sum(len(b["capabilities"]) for b in self._badges.values())
        module_counts = {}
        for b in self._badges.values():
            for module in b["capabilities"]:
                module_counts[module] = module_counts.get(module, 0) + 1
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "room_id": self.room_id,
            "score": self._calculate_score(),
            "commands_passed": self._scores["passed"],
            "commands_failed": self._scores["failed"],
            "num_badges": num_badges,
            "total_modules": total_modules,
            "badges": {
                bid: list(b["capabilities"].keys())
                for bid, b in self._badges.items()
            },
            "module_counts": module_counts,
        }
        try:
            entries = json.loads(LEADERBOARD_PATH.read_text()) if LEADERBOARD_PATH.exists() else []
            entries.append(entry)
            entries.sort(key=lambda e: e["score"], reverse=True)
            LEADERBOARD_PATH.write_text(json.dumps(entries, indent=2))
        except Exception:
            pass

    def _check_all_dismissed(self):
        active = set(self._badges)
        if not active or active <= self._dismissed:
            self._state = "waiting"
            self._dismissed = set()
            self._round_started_at = None

    def _time_remaining_s(self):
        if self._state != "in-round" or self._round_started_at is None:
            return None
        return max(0.0, ROUND_DURATION_S - (self._now() - self._round_started_at))

    def _poll_response(self, badge_id):
        self._check_expiry()
        in_round = self._state == "in-round"
        return {
            "room_id": self.room_id,
            "room_state": self._state,
            "time_remaining_s": self._time_remaining_s(),
            "assignment": self._assignment_for(badge_id) if in_round else None,
            "display": self._select_instruction(badge_id) if in_round else None,
            "scores": self._scores,
            "badge_scores": {self._colours[bid]: s for bid, s in self._badge_scores.items() if bid in self._colours},
            "badge_count": len(self._badges),
            "colour": self._colours.get(badge_id),
            "session_token": self._session_tokens.get(badge_id),
        }
