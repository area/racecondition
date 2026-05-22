from .base import CommandStatus, HexpansionModule
from .MegaDrive import MegaDriveModule
from .GPS import GPSModule
from .Tildagon2024 import Tildagon2024Module


MODULES = [
	Tildagon2024Module(),
	MegaDriveModule(),
	GPSModule(),
]


def get_connected_modules(hexpansions):
	connected = []
	for module in MODULES:
		if module.is_connected(hexpansions):
			connected.append(module)
	return connected


__all__ = [
	"HexpansionModule",
	"MegaDriveModule",
	"GPSModule",
	"Tildagon2024Module",
	"CommandStatus",
	"get_connected_modules",
]
