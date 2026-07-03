import random

# Flavour pool for button-style commands ("Smash A"). Shared by any module
# whose commands are plain presses; gesture-style phrases live with their module.
PRESS_VERBS = ("Press", "Hit", "Push", "Smash", "Bash")


def random_verb(verbs):
    # getrandbits is the one PRNG primitive guaranteed on the badge's
    # MicroPython build (see lib/aiohttp_ws.py); random.choice may be absent.
    return verbs[random.getrandbits(16) % len(verbs)]
