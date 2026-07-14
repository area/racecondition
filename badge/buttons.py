from events.input import BUTTON_TYPES


def is_cancel(event):
    # The event carries the physical frontboard button; the logical CANCEL
    # button sits in its ancestry, which Button.__contains__ walks. Matching
    # on ancestry rather than attribute layout survives firmware refactors —
    # the parent -> parents change in tildagonOS commit 8aa7bd8 silently
    # broke the previous implementation, which read `button.parent` and
    # matched on names.
    return BUTTON_TYPES["CANCEL"] in event.button
