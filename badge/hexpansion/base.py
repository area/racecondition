from ..hexpansion_names import get_friendly_name


class CommandStatus:
    PASSED = "passed"
    FAILED = "failed"
    WAITING = "waiting"


class HexpansionModule:
    VID = None
    PID = None

    COMMAND_OPTIONS = []

    @classmethod
    def friendly_name(cls):
        return get_friendly_name(cls.VID, cls.PID)

    # Turn a bare command token into the phrase shown large on screen and read
    # aloud ("a" -> "Smash a"). Display-only flavour: the command sent to and
    # received from the server is always the bare token, so this never affects
    # results. A classmethod because the instruction describes another badge's
    # assignment, whose module may not be plugged into this badge — we only have
    # the class, looked up by name. Default is no decoration; modules with
    # button- or gesture-style commands override.
    @classmethod
    def decorate(cls, command):
        return command

    # I would expect every hexpansion to need to override one, or both, of the on_button_down and
    # check_command methods. If you override on_button_down, you want to set self.last_status to CommandStatus.PASSED
    # when the command is successfully completed. If you override check_command, you want to return CommandStatus.PASSED
    # when the command is successfully completed, and CommandStatus.WAITING otherwise.
    def on_button_down(self, event):
        pass

    # Override only if the module needs to know when a button is released — e.g.
    # to track which buttons are currently held for a chord/diagonal. Most
    # modules only care about presses and can ignore this. Forwarded for every
    # non-cancel button release while a round is in progress (see app.py).
    def on_button_up(self, event):
        pass

    def check_command(self) -> str:
        return self.last_status

    # This only needs overriding if the hexpansion's capabilities depend on something other than being
    # plugged in
    def get_capabilities(self):
        return {
            "module": self.friendly_name(),
            "commands": list(self.COMMAND_OPTIONS),
        }

    # Will need overriding if, on receiving a command, some action needs to occur to prepare the hexpansion
    # to receive that command
    def set_command(self, command):
        if command not in self.COMMAND_OPTIONS:
            raise ValueError("Unsupported command '{}' for {}".format(command, self.friendly_name()))
        self.current_command = command
        self.last_status = CommandStatus.WAITING
        return self.current_command

    # This method is used to determine if the hexpansion is connected. It should be overridden if the hexpansion's connection status
    # depends on something other than being plugged in. Struggling to see what that might be, but it's here!
    def is_connected(self, hexpansions):
        for item in hexpansions.values():
            if item["known"] and item["name"] == self.friendly_name():
                return True
        return False

    def __init__(self):
        self.reset()

    def reset(self):
        self.current_command = None
        self.last_status = CommandStatus.WAITING



