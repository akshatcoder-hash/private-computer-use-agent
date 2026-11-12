"""Capture the work area and send mouse and keyboard events. macOS only."""
import ctypes
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

import Quartz


@dataclass(frozen=True)
class Region:
    """A rectangle of the main display, in points."""
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def parse(cls, text: str) -> "Region":
        return cls(*(int(part) for part in text.split(",")))


def display_size() -> tuple[int, int]:
    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return int(bounds.size.width), int(bounds.size.height)


def missing_permissions() -> list[str]:
    missing = []
    if not Quartz.CGPreflightScreenCaptureAccess():
        missing.append("Screen Recording")
    services = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    if not services.AXIsProcessTrusted():
        missing.append("Accessibility")
    return missing


def capture(region: Region) -> bytes:
    """PNG of the region. The file lives in a private temp directory and is deleted on return."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "screen.png")
        subprocess.run(
            ["screencapture", "-x", "-R", f"{region.x},{region.y},{region.width},{region.height}", path],
            check=True,
        )
        with open(path, "rb") as stream:
            return stream.read()


def _mouse(kind, point):
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft))


def click(x: float, y: float):
    """Glide to the point, then click, so a viewer can follow the cursor."""
    start = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    for i in range(1, 16):
        t = i / 15
        t = t * t * (3 - 2 * t)
        _mouse(Quartz.kCGEventMouseMoved, (start.x + (x - start.x) * t, start.y + (y - start.y) * t))
        time.sleep(0.012)
    time.sleep(0.15)
    _mouse(Quartz.kCGEventLeftMouseDown, (x, y))
    time.sleep(0.06)
    _mouse(Quartz.kCGEventLeftMouseUp, (x, y))


def type_text(text: str):
    for character in text:
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(character.encode("utf-16-le")) // 2, character)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.025)


KEYCODES = {"enter": 36, "return": 36, "tab": 48, "escape": 53, "space": 49, "backspace": 51,
            "up": 126, "down": 125, "left": 123, "right": 124, "pagedown": 121, "pageup": 116,
            "a": 0, "c": 8, "v": 9, "l": 37, "t": 17, "w": 13, "f": 3, "n": 45, "s": 1}
MODIFIERS = {"cmd": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift,
             "alt": Quartz.kCGEventFlagMaskAlternate, "ctrl": Quartz.kCGEventFlagMaskControl}


def press(key: str):
    """Press a key such as "enter" or a chord such as "cmd+a"."""
    *modifiers, name = key.lower().replace(" ", "").split("+")
    if name not in KEYCODES or any(m not in MODIFIERS for m in modifiers):
        raise ValueError(f"unsupported key: {key}")
    flags = 0
    for modifier in modifiers:
        flags |= MODIFIERS[modifier]
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, KEYCODES[name], down)
        Quartz.CGEventSetFlags(event, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.04)


def scroll(direction: str, x: float, y: float):
    _mouse(Quartz.kCGEventMouseMoved, (x, y))
    lines = -8 if direction == "down" else 8
    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines))
