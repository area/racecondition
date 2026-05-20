from .MegaDrive import CommandStatus, MegaDriveModule


MODULES = [
	MegaDriveModule(),
]


def get_connected_modules(hexpansions):
	connected = []
	for module in MODULES:
		if module.is_connected(hexpansions):
			connected.append(module)
	return connected


__all__ = ["MegaDriveModule", "CommandStatus", "get_connected_modules"]
