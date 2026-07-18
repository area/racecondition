from .base import CommandStatus
from .MegaDrive import MegaDriveModule
from .GPS import GPSModule
from .Keyboard import KeyboardModule
from .Tildagon2024 import Tildagon2024Module
from .Tildagon2026 import Tildagon2026Module


MODULE_TYPES = [
	Tildagon2024Module,
	Tildagon2026Module,
	MegaDriveModule,
	GPSModule,
	# KeyboardModule,
]


def decorate_command(module_name, command):
	# Look the module up by friendly name (the instruction may be for a module
	# not plugged into this badge, so we work off the class, not an instance)
	# and let it turn the bare command into its display phrase. Unknown modules
	# fall back to the raw command.
	if not command:
		return command
	for module_type in MODULE_TYPES:
		if module_type.friendly_name() == module_name:
			return module_type.decorate(command)
	return command


class ModuleRegistry:
	def __init__(self, module_types=None):
		self.module_types = module_types or MODULE_TYPES
		self._connected_modules = {}

	def scan(self, hexpansions):
		connected = {}
		for module_type in self.module_types:
			module = None
			for m in self._connected_modules.values():
				if type(m) is module_type:
					module = m
					break
			if module is None:
				module = module_type()
			if module.is_connected(hexpansions):
				connected[module.friendly_name()] = module

		for name, module in self._connected_modules.items():
			if name not in connected:
				module.reset()

		self._connected_modules = connected
		return self.connected_modules()

	def connected_modules(self):
		return list(self._connected_modules.values())

	def get_by_name(self, module_name):
		return self._connected_modules.get(module_name)

	def get_capabilities(self):
		return [module.get_capabilities() for module in self.connected_modules()]

	def reset_connected(self):
		for module in self._connected_modules.values():
			module.reset()


__all__ = [
	"CommandStatus",
	"ModuleRegistry",
	"decorate_command",
]
