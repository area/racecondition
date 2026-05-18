from .MegaDrive import MegaDriveModule


MODULES = [
	MegaDriveModule(),
]


def get_connected_modules(hexpansions):
	connected = []
	for module in MODULES:
		if module.is_connected(hexpansions):
			connected.append(module)
	return connected
