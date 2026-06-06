from .hexpansion import CommandStatus


class GameSession:
    def __init__(self):
        self.room_id = 1
        self.room_state = None  # None | "waiting" | "in-round" | "finished"
        self.cancel_hold_start = None
        self.expected_module = None
        self.expected_command_id = None
        self.expected_command = None
        self.assignment_time_remaining_s = None
        self.assignment_timeout_s = None
        self.assignment_updated_ms = None
        self.display_module_name = None
        self.display_command = None
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
        self.ready_count = 0
        self.is_ready = False
        self.dismissed_count = 0
        self.is_dismissed = False

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
        self.last_poll_ms = None
        self.score_pass = 0
        self.score_fail = 0
        self.badge_count = 0
        self.time_remaining_s = None
        self.server_scores = {"passed": 0, "failed": 0}

    def stop_room(self):
        self.room_state = None
        self.cancel_hold_start = None
        self.clear_assignment()
        self.clear_display()
        self.pending_result = None
        self.last_poll_ms = None
        self.badge_count = 0
        self.time_remaining_s = None
        self.badge_scores = {}

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
        command = display.get("command")
        colour = display.get("target_colour")
        if colour:
            self.display_command = "{}: {}".format(colour[0].upper() + colour[1:], command)
        else:
            self.display_command = command
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

    def apply_poll_response(self, data):
        self.pending_result = None
        self.set_room_state(data.get("room_state", self.room_state))
        self.badge_count = data.get("badge_count", 0)
        self.time_remaining_s = data.get("time_remaining_s")
        self.server_scores = data.get("scores", self.server_scores)
        if data.get("badge_scores"):
            self.badge_scores = data["badge_scores"]
        if data.get("ready_count") is not None:
            self.ready_count = data["ready_count"]
        if data.get("is_ready") is not None:
            self.is_ready = data["is_ready"]
        if data.get("dismissed_count") is not None:
            self.dismissed_count = data["dismissed_count"]
        if data.get("is_dismissed") is not None:
            self.is_dismissed = data["is_dismissed"]
        colour = data.get("colour")
        if colour and colour != self.badge_colour:
            self.badge_colour = colour
            return colour
        return None

    def format_remaining(self):
        if self.time_remaining_s is None:
            return "--:--"
        t = max(0, int(self.time_remaining_s))
        return "{:02d}:{:02d}".format(t // 60, t % 60)
