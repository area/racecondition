def is_cancel(event):
    button = getattr(event.button, "parent", None) or event.button
    return button.name.lower() == "cancel"
