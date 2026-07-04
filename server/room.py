import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock

log = logging.getLogger(__name__)

from leaderboard import SqliteLeaderboard

STALE_BADGE_SECONDS = 20
ROUND_DURATION_S = 120
# Each assignment's timeout ramps down linearly over the round, from
# ASSIGNMENT_TIMEOUT_S at the start to ASSIGNMENT_TIMEOUT_FLOOR_S at the end, so
# play tightens toward the finish. The value is fixed per assignment at the
# moment it is issued (stored on the Assignment), so its countdown stays stable.
ASSIGNMENT_TIMEOUT_S = 15
ASSIGNMENT_TIMEOUT_FLOOR_S = 10
# Mirror of COLOURS in badge/constants.py (the canonical palette, which maps
# these to RGB and explains why red/green are excluded). The badge runs a
# separate runtime and the server image ships only server/, so the list is
# duplicated here; tests/test_colour_sync.py fails if the two drift apart.
COLOURS = ["white", "cyan", "blue", "yellow", "purple", "orange"]
MAX_BADGES = len(COLOURS)


def _normalize_capabilities(capabilities):
    normalized = {}
    if not isinstance(capabilities, list):
        return normalized
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        module = item.get("module")
        commands = item.get("commands")
        if not isinstance(module, str) or not isinstance(commands, list):
            continue
        cleaned = [c for c in commands if isinstance(c, str) and c]
        if cleaned:
            normalized[module] = tuple(cleaned)
    return normalized


@dataclass
class Assignment:
    id: str
    module: str
    command: str
    issued_at: float
    timeout_s: float


@dataclass
class BadgeSlot:
    capabilities: dict
    last_seen: float
    colour: str
    score: dict
    last_sent_gen: int | None = None
    pinned_target: str | None = None
    assignment: Assignment | None = None


class Room:
    def __init__(self, room_id, leaderboard=None, user_registry=None):
        self.room_id = room_id
        self.created_at = time.monotonic()
        self._lock = Lock()
        self._leaderboard = leaderboard if leaderboard is not None else SqliteLeaderboard()
        self._user_registry = user_registry
        self._reset_state()

    # ------------------------------------------------------------------ public

    def join(self, badge_id, capabilities):
        with self._lock:
            self._prune_stale()
            if badge_id not in self._badges and len(self._badges) >= MAX_BADGES:
                return {"room_id": self.room_id, "error": "Room is full"}
            self._set_badge(badge_id, _normalize_capabilities(capabilities))
            return self._poll_response(badge_id)

    def poll(self, badge_id, capabilities, result=None):
        with self._lock:
            self._prune_stale()
            # A poll from an unknown badge is a re-add (e.g. stale-pruned but its
            # websocket survived), so it must pass the same capacity check as
            # join — otherwise a full room grows past MAX_BADGES and _set_badge's
            # colour fallback hands out duplicates.
            if badge_id not in self._badges and len(self._badges) >= MAX_BADGES:
                return {"room_id": self.room_id, "error": "Room is full"}
            norm = _normalize_capabilities(capabilities) if capabilities is not None else None
            self._set_badge(badge_id, norm)
            self._check_expiry()
            # The websocket authenticates the connection by secret_id and derives
            # badge_id from it, so a result here provably comes from the badge it
            # claims to be — no per-result token check is needed.
            if result is not None and self._state == "in-round":
                self._apply_result(badge_id, result)
            return self._poll_response(badge_id)

    def leave(self, badge_id):
        with self._lock:
            self._prune_stale()
            if self._badges.pop(badge_id, None) is not None:
                self._command_pool_cache = None
            self._ready.discard(badge_id)
            self._dismissed.discard(badge_id)
            if not self._badges:
                self._reset_state()
            else:
                self._players_generation += 1
                if self._state == "finished":
                    self._check_all_dismissed()
                elif self._state == "waiting":
                    self._check_all_ready()
            badge_count = len(self._badges)
        return {"room_id": self.room_id, "status": "left", "badge_count": badge_count}

    def start_round(self, badge_id):
        with self._lock:
            self._prune_stale()
            if self._state != "waiting":
                return {"room_id": self.room_id, "error": "Round already in progress"}
            if badge_id not in self._badges:
                return {"room_id": self.room_id, "error": "Badge not in room"}
            if badge_id not in self._ready:
                self._ready.add(badge_id)
                # Readiness is part of the players payload, which is only
                # re-sent when the generation moves.
                self._players_generation += 1
            if not (set(self._badges.keys()) - self._ready):
                self._start_round_locked()
                return {"room_id": self.room_id, "status": "started", "room_state": "in-round"}
        return {"room_id": self.room_id, "status": "ready", "room_state": "waiting", "ready_count": len(self._ready)}

    def unready(self, badge_id):
        with self._lock:
            self._prune_stale()
            if self._state != "waiting":
                return {"room_id": self.room_id, "error": "Round already in progress"}
            if badge_id not in self._badges:
                return {"room_id": self.room_id, "error": "Badge not in room"}
            if badge_id in self._ready:
                self._ready.discard(badge_id)
                self._players_generation += 1
            return {"room_id": self.room_id, "status": "unready", "room_state": "waiting", "ready_count": len(self._ready)}

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

    def undismiss_score(self, badge_id):
        with self._lock:
            if self._state == "finished":
                self._dismissed.discard(badge_id)
            return {"room_id": self.room_id, "status": "ok", "room_state": self._state}

    def admin_snapshot(self):
        with self._lock:
            self._prune_stale()
            now = self._now()
            badges = [
                {
                    "badge_id": bid,
                    "colour": slot.colour,
                    "module_count": len(slot.capabilities),
                    "modules": list(slot.capabilities.keys()),
                    "last_seen_s": round(now - slot.last_seen, 1),
                }
                for bid, slot in self._badges.items()
            ]
            assignments = [
                {
                    "id": slot.assignment.id,
                    "target_badge_id": bid,
                    "target_colour": slot.colour,
                    "module": slot.assignment.module,
                    "command": slot.assignment.command,
                    "age_s": round(now - slot.assignment.issued_at, 1),
                }
                for bid, slot in self._badges.items()
                if slot.assignment is not None
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
        self._module_scores = {}
        self._next_assignment_id = 1
        self._scores = {"passed": 0, "failed": 0}
        self._state = "waiting"
        self._round_started_at = None
        self._dismissed = set()
        self._ready = set()
        self._players_generation = 0
        self._command_pool_cache = None
        self._round_rank = None  # (rank, total_games) once a round is recorded

    def _now(self):
        return time.monotonic()

    def _start_round_locked(self):
        self._state = "in-round"
        self._round_started_at = self._now()
        self._scores = {"passed": 0, "failed": 0}
        self._module_scores = {}
        self._ready = set()
        self._round_rank = None
        # Ready flags travel in the players payload; clearing them must
        # trigger a re-send.
        self._players_generation += 1
        for slot in self._badges.values():
            slot.score = {"passed": 0, "failed": 0}
            slot.assignment = None
            slot.pinned_target = None

    def _check_all_ready(self):
        if self._state == "waiting" and self._badges and not (set(self._badges.keys()) - self._ready):
            self._start_round_locked()

    def _prune_stale(self):
        cutoff = self._now() - STALE_BADGE_SECONDS
        stale = [bid for bid, slot in self._badges.items() if slot.last_seen < cutoff]
        for bid in stale:
            del self._badges[bid]
            self._dismissed.discard(bid)
            self._ready.discard(bid)
        if stale:
            self._command_pool_cache = None
        if not self._badges:
            self._reset_state()
        elif stale:
            self._players_generation += 1
            if self._state == "finished":
                self._check_all_dismissed()
            elif self._state == "waiting":
                self._check_all_ready()

    def _set_badge(self, badge_id, capabilities):
        if badge_id not in self._badges:
            used = {slot.colour for slot in self._badges.values()}
            colour = next((c for c in COLOURS if c not in used), COLOURS[0])
            self._badges[badge_id] = BadgeSlot(
                capabilities=capabilities or {},
                last_seen=self._now(),
                colour=colour,
                score={"passed": 0, "failed": 0},
            )
            self._players_generation += 1
            self._command_pool_cache = None
        else:
            slot = self._badges[badge_id]
            if capabilities is not None:
                slot.capabilities = capabilities
                self._command_pool_cache = None
            slot.last_seen = self._now()

    def _command_pool(self):
        # Cached: rebuilt lazily only after a change to badge membership or
        # capabilities invalidates it (see _command_pool_cache = None below).
        # Every in-round poll calls this, but the underlying set only moves when
        # a badge joins, leaves, is pruned, or pushes new capabilities.
        if self._command_pool_cache is None:
            self._command_pool_cache = [
                (module, command)
                for slot in self._badges.values()
                for module, commands in slot.capabilities.items()
                for command in commands
            ]
        return self._command_pool_cache

    def _badge_can_run(self, badge_id, module, command):
        slot = self._badges.get(badge_id)
        return slot is not None and command in slot.capabilities.get(module, ())

    def _assignment_timeout(self):
        # Linear ramp from ASSIGNMENT_TIMEOUT_S (round start) to
        # ASSIGNMENT_TIMEOUT_FLOOR_S (round end), clamped at the floor.
        if self._round_started_at is None:
            return float(ASSIGNMENT_TIMEOUT_S)
        elapsed = self._now() - self._round_started_at
        frac = max(0.0, min(1.0, elapsed / ROUND_DURATION_S))
        return ASSIGNMENT_TIMEOUT_S - (ASSIGNMENT_TIMEOUT_S - ASSIGNMENT_TIMEOUT_FLOOR_S) * frac

    def _assignment_for(self, badge_id):
        now = self._now()
        slot = self._badges.get(badge_id)
        if slot is None:
            return None
        existing = slot.assignment
        if existing is not None:
            age = now - existing.issued_at
            if age < existing.timeout_s:
                return {
                    "id": existing.id,
                    "module": existing.module,
                    "command": existing.command,
                    "time_remaining_s": existing.timeout_s - age,
                    "timeout_s": existing.timeout_s,
                }
            log.debug("room=%s badge=%s timed out module=%s command=%s", self.room_id, badge_id[-6:], existing.module, existing.command)
            self._scores["failed"] += 1
            slot.score["failed"] += 1
            self._module_scores.setdefault(existing.module, {"passed": 0, "failed": 0})["failed"] += 1
            slot.assignment = None

        candidates = [(m, c) for m, c in self._command_pool() if self._badge_can_run(badge_id, m, c)]
        if not candidates:
            log.debug("room=%s badge=%s no candidates available", self.room_id, badge_id[-6:])
            return None
        module, command = random.choice(candidates)
        assignment_id = "{}-{}".format(id(self), self._next_assignment_id)
        self._next_assignment_id += 1
        timeout = self._assignment_timeout()
        log.debug("room=%s badge=%s assigned module=%s command=%s id=%s", self.room_id, badge_id[-6:], module, command, assignment_id)
        slot.assignment = Assignment(id=assignment_id, module=module, command=command, issued_at=now, timeout_s=timeout)
        return {
            "id": assignment_id,
            "module": module,
            "command": command,
            "time_remaining_s": timeout,
            "timeout_s": timeout,
        }

    def _select_instruction(self, badge_id):
        now = self._now()
        slot = self._badges.get(badge_id)
        if slot is None:
            return None
        pinned = slot.pinned_target
        if pinned is not None and pinned in self._badges and self._badges[pinned].assignment is not None:
            target_id = pinned
            assignment = self._badges[pinned].assignment
        else:
            all_assignments = [(bid, s.assignment) for bid, s in self._badges.items() if s.assignment is not None]
            if not all_assignments:
                return None
            other = [(tid, a) for tid, a in all_assignments if tid != badge_id]
            target_id, assignment = random.choice(other if other else all_assignments)
            if target_id != badge_id:
                slot.pinned_target = target_id
            else:
                slot.pinned_target = None
        time_remaining = max(0.0, assignment.timeout_s - (now - assignment.issued_at))
        target_slot = self._badges.get(target_id)
        return {
            "id": assignment.id,
            "module": assignment.module,
            "command": assignment.command,
            "target_colour": target_slot.colour if target_slot else None,
            "time_remaining_s": time_remaining,
            "timeout_s": assignment.timeout_s,
        }

    def _apply_result(self, badge_id, result):
        if not isinstance(result, dict):
            return
        slot = self._badges.get(badge_id)
        if slot is None or slot.assignment is None:
            return
        if result.get("assignment_id") != slot.assignment.id:
            return
        status = result.get("status")
        if status not in ("passed", "failed"):
            return
        self._scores[status] += 1
        slot.score[status] += 1
        self._module_scores.setdefault(slot.assignment.module, {"passed": 0, "failed": 0})[status] += 1
        slot.assignment = None

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
        total_modules = sum(len(slot.capabilities) for slot in self._badges.values())
        commands_passed = self._scores["passed"]
        avg_modules = total_modules / num_badges
        return round(commands_passed * math.sqrt(num_badges) * avg_modules, 2)

    def _record_score(self):
        num_badges = len(self._badges)
        total_modules = sum(len(slot.capabilities) for slot in self._badges.values())
        module_counts = {}
        for slot in self._badges.values():
            for module in slot.capabilities:
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
                bid: list(slot.capabilities.keys())
                for bid, slot in self._badges.items()
            },
            "module_counts": module_counts,
            "module_scores": dict(self._module_scores),
            "badge_scores": {bid: slot.score for bid, slot in self._badges.items()},
        }
        try:
            self._leaderboard.record(entry)
            self._round_rank = self._leaderboard.rank_of_score(entry["score"])
        except Exception as exc:
            print("[Room] Leaderboard write failed: {}".format(exc))

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

    def _username(self, badge_id):
        if self._user_registry:
            return self._user_registry.get(badge_id)
        return None

    def _poll_response(self, badge_id):
        self._check_expiry()
        in_round = self._state == "in-round"
        current_gen = self._players_generation
        slot = self._badges.get(badge_id)
        if slot is not None and slot.last_sent_gen != current_gen:
            slot.last_sent_gen = current_gen
            players = [
                {
                    "colour": s.colour,
                    "username": self._username(bid) or s.colour,
                    "ready": bid in self._ready,
                }
                for bid, s in self._badges.items()
            ]
        else:
            players = None
        has_caps = bool(slot and slot.capabilities)
        return {
            "room_id": self.room_id,
            "room_state": self._state,
            "time_remaining_s": self._time_remaining_s(),
            "assignment": self._assignment_for(badge_id) if in_round else None,
            "display": self._select_instruction(badge_id) if in_round else None,
            "scores": self._scores,
            "badge_scores": {s.colour: s.score for s in self._badges.values()},
            "badge_count": len(self._badges),
            "colour": slot.colour if slot else None,
            "ready_count": len(self._ready) if self._state == "waiting" else None,
            "is_ready": (badge_id in self._ready) if self._state == "waiting" else None,
            "dismissed_count": len(self._dismissed) if self._state == "finished" else None,
            "is_dismissed": (badge_id in self._dismissed) if self._state == "finished" else None,
            "overall_score": self._calculate_score() if self._state == "finished" else None,
            "rank": self._round_rank[0] if self._state == "finished" and self._round_rank else None,
            "total_games": self._round_rank[1] if self._state == "finished" and self._round_rank else None,
            "players": players,
            "need_capabilities": not has_caps,
        }
