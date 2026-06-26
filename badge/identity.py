import time


def build_secret_id():
	try:
		from tildagon import HMAC
		return HMAC.digest(HMAC.HMAC_KEY1, b"Race Condition").hex()
	except Exception:
		pass
	try:
		import machine
		import ubinascii
		return ubinascii.hexlify(machine.unique_id()).decode("utf-8")
	except Exception:
		return "badge-{}".format(time.ticks_ms())


def derive_public_id(secret_id):
	import uhashlib
	import ubinascii
	h = uhashlib.sha256()
	h.update(secret_id.encode())
	return ubinascii.hexlify(h.digest()[:8]).decode()
