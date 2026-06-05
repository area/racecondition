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
        self.pending_result = None
        self.last_poll_ms = None
        self.score_pass = 0
        self.score_fail = 0
        self.badge_colour = None
        self.badge_count = 0
        self.time_remaining_s = None
        self.server_scores = {"passed": 0, "failed": 0}

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

    def clear_display(self):
        self.display_module_name = None
        self.display_command = None

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

    def set_assignment(self, module, assignment_id, command):
        self.expected_module = module
        self.expected_command_id = assignment_id
        self.expected_command = command

    def set_display(self, display):
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

    def format_remaining(self):
        if self.time_remaining_s is None:
            return "--:--"
        t = max(0, int(self.time_remaining_s))
        return "{:02d}:{:02d}".format(t // 60, t % 60)
