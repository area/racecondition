import random


class MegaDriveModule:
    FRIENDLY_NAME = "MegaDrive"
    COMMAND_OPTIONS = ["start", "a", "up", "down", "left", "right", "b", "c"]

    def is_connected(self, hexpansions):
        for item in hexpansions.values():
            if item["known"] and item["name"] == self.FRIENDLY_NAME:
                return True
        return False

    def choose_command(self):
        return random.choice(self.COMMAND_OPTIONS)

    def get_supported_commands(self):
        return list(self.COMMAND_OPTIONS)

    def is_supported_command(self, button_name):
        if not button_name:
            return False
        valid = tuple(command.upper() for command in self.COMMAND_OPTIONS)
        return button_name.upper() in valid

    def get_command_from_event(self, event):
        button_name = self.get_button_name(event)
        if not self.is_supported_command(button_name):
            return None
        return button_name.lower()

    def get_button_name(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None

        for attr in ("name", "_name", "label"):
            value = getattr(button, attr, None)
            if isinstance(value, str) and value:
                return value.upper()

        text = str(button)
        if text:
            return text.upper()

        return None

    def get_button_source(self, event):
        button = getattr(event, "button", None)
        if button is None:
            return None

        for attr in ("source", "_source", "app", "_app", "origin", "_origin"):
            value = getattr(button, attr, None)
            if isinstance(value, str) and value:
                return value

        return str(button)

    def is_button(self, event, expected_name):
        name = self.get_button_name(event)
        if name != expected_name.upper():
            return False

        source = self.get_button_source(event)
        if not source:
            return False

        source_upper = source.upper()
        return ("SEGA" in source_upper) or ("MEGADRIVE" in source_upper)

    def is_app_instance(self, instance):
        class_name = instance.__class__.__name__.lower()
        if class_name in ("sega", "megadrive", "segacontroller"):
            return True

        module_name = getattr(instance.__class__, "__module__", "").lower()
        if "0x4291" in module_name and "5e6a" in module_name:
            return True

        rendered = str(instance).lower()
        return "sega" in rendered or "megadrive" in rendered



# Backward-compatible alias for older imports.
MegaDriveController = MegaDriveModule
