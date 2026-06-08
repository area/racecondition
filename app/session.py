from .hexpansion import CommandStatus


class GameSession:
    def __init__(self):
        self.room_id = 1
        self.room_state = None  # None | "waiting" | "in-round" | "finished"
        self.session_token = None
        self.cancel_hold_start = None
        self.expected_module = None
        self.expected_command_id = None
        self.expected_command = None
        self.assignment_time_remaining_s = None
        self.assignment_timeout_s = None
        self.assignment_updated_ms = None
        self.display_module_name = None
        self.display_command = None
        self.display_target_colour = None
        self.display_time_remaining_s = None
        self.display_timeout_s = None
        self.display_updated_ms = None
        self.pending_result = None
        self.last_poll_ms = None
        self.score_pass = 0
        self.score_fail = 0
        self.badge_colour = None
        self.badge_count = 0
        self.time_remaining_s = None
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

    @property
    def in_round(self):
        return self.room_state == "in-round"

    def clear_assignment(self):
        self.expected_module = None
        self.expected_command_id = None
        self.expected_command = None
        self.assignment_time_remaining_s = None
        self.assignment_timeout_s = None
        self.assignment_updated_ms = None

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
        self.session_token = None
        self.cancel_hold_start = None
        self.clear_assignment()
        self.clear_display()
        self.pending_result = None
        self.last_poll_ms = None
        self.score_pass = 0
        self.score_fail = 0
        self.badge_count = 0
        self.time_remaining_s = None
        self.server_scores = {"passed": 0, "failed": 0}
        self.overall_score = None

    def stop_room(self):
        self.room_state = None
        self.session_token = None
        self.cancel_hold_start = None
        self.clear_assignment()
        self.clear_display()
        self.pending_result = None
        self.last_poll_ms = None
        self.badge_count = 0
        self.time_remaining_s = None
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
            self.score_pass = 0
            self.score_fail = 0
            self.badge_scores = {}
            self.ready_count = 0
            self.is_ready = False
            self.dismissed_count = 0
            self.is_dismissed = False

    def set_assignment(self, module, assignment_id, command, time_remaining_s=None, timeout_s=None, now_ms=None):
        self.expected_module = module
        self.expected_command_id = assignment_id
        self.expected_command = command
        self.assignment_time_remaining_s = time_remaining_s
        self.assignment_timeout_s = timeout_s
        self.assignment_updated_ms = now_ms

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
        if status == CommandStatus.PASSED:
            self.score_pass += 1
        elif status == CommandStatus.FAILED:
            self.score_fail += 1
        else:
            return None
        result = {
            "assignment_id": self.expected_command_id,
            "status": status,
            "module": self.expected_module.FRIENDLY_NAME,
            "command": self.expected_command,
        }
        self.clear_assignment()
        return result

    def apply_poll_response(self, data, now_ms=None, module_lookup=None):
        self.pending_result = None
        self.set_room_state(data.get("room_state", self.room_state))
        self.badge_count = data.get("badge_count", 0)
        self.time_remaining_s = data.get("time_remaining_s")
        self.server_scores = data.get("scores", self.server_scores)
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
        token = data.get("session_token")
        if token:
            self.session_token = token
        if self.room_state == "in-round" and module_lookup is not None:
            self._apply_assignment(data.get("assignment"), now_ms, module_lookup)
            self.set_display(data.get("display"), now_ms=now_ms)
        colour = data.get("colour")
        if colour and colour != self.badge_colour:
            self.badge_colour = colour
            return colour
        return None

    def _apply_assignment(self, assignment, now_ms, module_lookup):
        if not assignment:
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
            try:
                module.set_command(command)
            except Exception:
                self.clear_assignment()
                return
        self.set_assignment(
            module, assignment_id, command,
            time_remaining_s=assignment.get("time_remaining_s"),
            timeout_s=assignment.get("timeout_s"),
            now_ms=now_ms,
        )

    def format_remaining(self):
        if self.time_remaining_s is None:
            return "--:--"
        t = max(0, int(self.time_remaining_s))
        return "{:02d}:{:02d}".format(t // 60, t % 60)
