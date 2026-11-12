"""The keys the agent may press. Pure data, no Quartz, so the planner prompt and the guard share one list.

Everything else is refused: other modifier chords (cmd+space opens Spotlight, cmd+tab switches app, cmd+q and
cmd+w close things, ctrl+* are system shortcuts) can leave the work window or destroy state.
"""

ALLOWED_KEYS = ("enter", "return", "tab", "escape", "backspace", "delete", "up", "down", "left", "right",
                "pageup", "pagedown", "space", "cmd+a")


def normalize_key(key: str) -> str:
    return (key or "").lower().replace(" ", "")


def check_key_allowed(key: str) -> str | None:
    """None if the key may be pressed, else the reason."""
    if normalize_key(key) in ALLOWED_KEYS:
        return None
    return f"key {key!r} is not allowed (allowed: {', '.join(ALLOWED_KEYS)})"
