import time

from .constants import CANCEL_HOLD_MS
from .hexpansion import CommandStatus


class GameSession:
    def __init__(self):
        self.room_id = 1
        self.room_state = None  # None | "waiting" | "in-round" | "finished"
        self.cancel_hold_start = None
        self.expected_module = None
        self.expected_command_id = None
        self.expected_command = None
        self.display_module_name = None
        self.display_command = None
        self.display_target_colour = None
        self.display_time_remaining_s = None
        self.display_timeout_s = None
        self.display_updated_ms = None
        self.pending_result = None
        self.assignment_timed_out = False
        self.badge_colour = None
        self.badge_count = 0
        self.time_remaining_s = None
        self.time_remaining_updated_ms = None
        self.server_scores = {"passed": 0, "failed": 0}
        self.badge_scores = {}
        self.overall_score = None
        self.ready_count = 0
        self.is_ready = False
        self.dismissed_count = 0
        self.is_dismissed = False
        self.players = []

    @property
    def in_game(self):
        return self.room_state is not None

    def cancel_hold_progress(self, now_ms):
        # Fraction (0..1) of the hold-to-leave gesture completed, or None when
        # the cancel button isn't being held. Drives the on-screen hold ring.
        if self.cancel_hold_start is None:
            return None
        held = time.ticks_diff(now_ms, self.cancel_hold_start)
        return max(0.0, min(1.0, held / CANCEL_HOLD_MS))

    @property
    def in_round(self):
        return self.room_state == "in-round"

    def clear_assignment(self):
        self.expected_module = None
        self.expected_command_id = None
        self.expected_command = None

    def clear_display(self):
        self.display_module_name = None
        self.display_command = None
        self.display_target_colour = None
        self.display_time_remaining_s = None
        self.display_timeout_s = None
        self.display_updated_ms = None

    def start_room(self, room_id):
        self.room_id = room_id
        self.room_state = "waiting"
        self.cancel_hold_start = None
        self.clear_assignment()
        self.clear_display()
        self.pending_result = None
        self.badge_count = 0
        self.time_remaining_s = None
        self.time_remaining_updated_ms = None
        self.server_scores = {"passed": 0, "failed": 0}
        self.overall_score = None

    def stop_room(self):
        self.room_state = None
        self.cancel_hold_start = None
        self.clear_assignment()
        self.clear_display()
        self.pending_result = None
        self.badge_count = 0
        self.time_remaining_s = None
        self.time_remaining_updated_ms = None
        self.badge_scores = {}
        self.players = []

    def set_room_state(self, state):
        if state == self.room_state:
            return
        self.room_state = state
        if state == "waiting":
            self.clear_assignment()
            self.clear_display()
            self.pending_result = None
            self.assignment_timed_out = False
            self.badge_scores = {}
            self.ready_count = 0
            self.is_ready = False
            self.dismissed_count = 0
            self.is_dismissed = False

    def set_assignment(self, module, assignment_id, command):
        self.expected_module = module
        self.expected_command_id = assignment_id
        self.expected_command = command

    def set_display(self, display, now_ms=None):
        if not display:
            self.clear_display()
            return
        self.display_module_name = display.get("module")
        self.display_command = display.get("command")
        colour = display.get("target_colour")
        self.display_target_colour = colour[0].upper() + colour[1:] if colour else None
        self.display_time_remaining_s = display.get("time_remaining_s")
        self.display_timeout_s = display.get("timeout_s")
        self.display_updated_ms = now_ms

    def build_result(self, status):
        if self.expected_module is None:
            return None
        if status not in (CommandStatus.PASSED, CommandStatus.FAILED):
            return None
        result = {
            "assignment_id": self.expected_command_id,
            "status": status,
            "module": self.expected_module.friendly_name(),
            "command": self.expected_command,
        }
        self.clear_assignment()
        return result

    def apply_poll_response(self, data, now_ms=None, module_lookup=None):
        # Delta-aware: the server may send only the fields that changed, so a
        # field is updated only when its key is actually present. An absent key
        # means "unchanged"; a present key with a null value is an explicit
        # reset. (A full state simply carries every key.)
        # pending_result is intentionally NOT touched here — the websocket
        # writer owns its lifecycle (see RaceConditionApp._flush_ws_outbox), so
        # an incoming state push can't drop a result before it has been sent.
        if "room_state" in data:
            self.set_room_state(data["room_state"])
        if "badge_count" in data:
            self.badge_count = data["badge_count"]
        if "time_remaining_s" in data:
            self.time_remaining_s = data["time_remaining_s"]
            self.time_remaining_updated_ms = now_ms
        if "scores" in data:
            self.server_scores = data["scores"]
        if data.get("badge_scores"):
            self.badge_scores = data["badge_scores"]
        if data.get("overall_score") is not None:
            self.overall_score = data["overall_score"]
        if data.get("ready_count") is not None:
            self.ready_count = data["ready_count"]
        if data.get("is_ready") is not None:
            self.is_ready = data["is_ready"]
        if data.get("dismissed_count") is not None:
            self.dismissed_count = data["dismissed_count"]
        if data.get("is_dismissed") is not None:
            self.is_dismissed = data["is_dismissed"]
        if data.get("players") is not None:
            self.players = data["players"]
        if self.room_state == "in-round" and module_lookup is not None:
            if "assignment" in data:
                self._apply_assignment(data["assignment"], module_lookup)
            if "display" in data:
                self.set_display(data["display"], now_ms=now_ms)
        colour = data.get("colour")
        if colour and colour != self.badge_colour:
            self.badge_colour = colour
            return colour
        return None

    def _apply_assignment(self, assignment, module_lookup):
        if not assignment:
            # An assignment vanishing while we still held one means the server
            # timed it out (we clear our own on a local pass, so expected is
            # already None in that case) — flag a fail for the LED feedback.
            if self.expected_command_id is not None:
                self.assignment_timed_out = True
            self.clear_assignment()
            return
        module_name = assignment.get("module")
        command = assignment.get("command")
        assignment_id = assignment.get("id")
        module = module_lookup(module_name)
        if not module:
            self.clear_assignment()
            return
        if self.expected_command_id != assignment_id:
            if self.expected_command_id is not None:
                self.assignment_timed_out = True
            try:
                module.set_command(command)
            except Exception:
                self.clear_assignment()
                return
        self.set_assignment(module, assignment_id, command)

    def remaining_seconds(self, now_ms=None):
        if self.time_remaining_s is None:
            return None
        remaining = self.time_remaining_s
        # The server only resends the round timer on a jump (round start, the
        # admin "hurry", round end); between those we count down locally from
        # the last value we were given.
        if now_ms is not None and self.time_remaining_updated_ms is not None:
            remaining -= time.ticks_diff(now_ms, self.time_remaining_updated_ms) / 1000
        return max(0, int(remaining))

    def format_remaining(self, now_ms=None):
        t = self.remaining_seconds(now_ms)
        if t is None:
            return "--:--"
        return "{:02d}:{:02d}".format(t // 60, t % 60)
