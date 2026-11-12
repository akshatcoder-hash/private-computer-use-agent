"""Capture the work area and send mouse and keyboard events. macOS only."""
import ctypes
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass

import Quartz

from .screen_keys import check_key_allowed, normalize_key


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


# ---------------------------------------------------------------------------------------------------------
# Guard: the agent may capture and act only while its own work window is on top.
#
# The work window's owner PID is the browser's MAIN process, the pid we launched (Chromium shows every
# window of one instance under the main process; helper processes own no windows). The panel is a second
# Chromium instance with its own user-data-dir, so it has a different pid and sits at x=1400, outside the
# work region. The pure functions below take a window list (front to back, as CGWindowListCopyWindowInfo
# returns it) so they can be tested without a screen.
#
# What is ignored, and only these:
#   * the menu bar band: the region is clipped to start below the "Menubar" window (layer 24), so the
#     menu bar, its status items (layer 25) and the Control Center never count as covering the work area.
#     They are system chrome, not another app's content.
#   * "Window Server" windows at cursor level (layer >= 2147483000): the pointer and its indicators.
#   * the Dock (owner "Dock", layer 20) in the REGION check only; a click point on the Dock is still blocked.
#   * windows that are fully transparent (alpha 0), empty, or behind the work window (never consulted).
# Everything else that intersects the region in front of the work window must belong to the work pid,
# whatever its layer: notification banners, other apps' floating panels, overlays. That fails closed.
# Some apps keep an invisible overlay window on screen (for example a dictation widget at layer 1000);
# those block the agent until the user opts that app in by name (AGENT_ALLOW_OVERLAYS or --allow-overlay).
# ---------------------------------------------------------------------------------------------------------
CURSOR_LAYER_MIN = 2147483000


@dataclass(frozen=True)
class Win:
    pid: int
    owner: str
    name: str
    layer: int
    alpha: float
    x: float
    y: float
    w: float
    h: float


def list_windows() -> list[Win]:
    """On-screen windows, front to back."""
    info = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID) or []
    out = []
    for w in info:
        b = w.get("kCGWindowBounds") or {}
        out.append(Win(int(w.get("kCGWindowOwnerPID", -1)), str(w.get("kCGWindowOwnerName", "?")), str(w.get("kCGWindowName", "") or ""),
                       int(w.get("kCGWindowLayer", 0)), float(w.get("kCGWindowAlpha", 1.0)),
                       float(b.get("X", 0)), float(b.get("Y", 0)), float(b.get("Width", 0)), float(b.get("Height", 0))))
    return out


def allowed_overlays_from_env() -> frozenset[str]:
    return frozenset(n.strip() for n in os.environ.get("AGENT_ALLOW_OVERLAYS", "").split(",") if n.strip())


def _describe(w: Win) -> str:
    return f"{w.owner}" + (f" ({w.name!r})" if w.name else "")


def _intersects(w: Win, x0: float, y0: float, x1: float, y1: float) -> bool:
    return w.w > 0 and w.h > 0 and w.x < x1 and w.x + w.w > x0 and w.y < y1 and w.y + w.h > y0


def _is_cursor(w: Win) -> bool:
    return w.owner == "Window Server" and w.layer >= CURSOR_LAYER_MIN


def _effective_top(windows: list[Win], region: Region) -> float:
    """Region top, moved below the menu bar if the menu bar overlaps it."""
    top = float(region.y)
    for w in windows:
        if w.owner == "Window Server" and w.layer == 24 and w.name == "Menubar" and _intersects(w, region.x, region.y, region.x + region.width, region.y + region.height):
            top = max(top, w.y + w.h)
    return top


def clip_below_menubar(windows: list[Win], region: Region) -> Region:
    """The part of region below the menu bar, so captures never contain other apps' menu bar status icons."""
    top = int(-(-_effective_top(windows, region) // 1))  # ceil
    if top <= region.y:
        return region
    return Region(region.x, top, region.width, region.y + region.height - top)


def work_region(region: Region) -> Region:
    return clip_below_menubar(list_windows(), region)


def check_region(windows: list[Win], region: Region, work_pid: int, allow: frozenset[str] = frozenset()) -> str | None:
    """None if the whole region is covered by a window of work_pid with nothing foreign in front of it; else the reason."""
    x0, x1, y1 = float(region.x), float(region.x + region.width), float(region.y + region.height)
    y0 = _effective_top(windows, region)
    blockers: list[str] = []
    for w in windows:
        if w.alpha <= 0:
            continue
        if w.pid == work_pid and w.layer == 0 and w.x <= x0 and w.y <= y0 and w.x + w.w >= x1 and w.y + w.h >= y1:
            if blockers:
                break
            return None
        if w.pid == work_pid or _is_cursor(w) or w.owner in allow or (w.owner == "Dock" and w.layer == 20):
            continue
        if _intersects(w, x0, y0, x1, y1) and _describe(w) not in blockers:
            blockers.append(_describe(w))
    if blockers:
        return "covered by " + ", ".join(blockers[:3]) + (" and more" if len(blockers) > 3 else "")
    mine = [w for w in windows if w.pid == work_pid and w.layer == 0 and w.alpha > 0]
    if not mine:
        return f"no on-screen window owned by the work browser (pid {work_pid})"
    return f"the work window does not cover the work area (its window is at {mine[0].x:.0f},{mine[0].y:.0f} {mine[0].w:.0f}x{mine[0].h:.0f})"


def check_point(windows: list[Win], x: float, y: float, work_pid: int, allow: frozenset[str] = frozenset()) -> str | None:
    """None if the topmost window at (x, y) belongs to work_pid; else the reason. Strict: the Dock and menu bar count."""
    for w in windows:
        if w.alpha <= 0 or _is_cursor(w) or w.owner in allow:
            continue
        if _intersects(w, x, y, x + 1e-6, y + 1e-6):
            return None if w.pid == work_pid else f"the point ({x:.0f}, {y:.0f}) is covered by {_describe(w)}"
    return f"no window at ({x:.0f}, {y:.0f})"


def frontmost_pid() -> int | None:
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app else None


def check_frontmost(work_pid: int) -> str | None:
    """Keyboard input goes to the focused app, not to a point: it must be the work browser."""
    pid = frontmost_pid()
    if pid == work_pid:
        return None
    from AppKit import NSRunningApplication
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) if pid else None
    return f"the focused app is {app.localizedName() if app else 'unknown'} (pid {pid}), not the work browser"


def guard_region(region: Region, work_pid: int, allow: frozenset[str] | None = None) -> str | None:
    allow = allowed_overlays_from_env() if allow is None else allow
    return check_region(list_windows(), region, work_pid, allow)


def guard_point(x: float, y: float, work_pid: int, allow: frozenset[str] | None = None) -> str | None:
    allow = allowed_overlays_from_env() if allow is None else allow
    return check_point(list_windows(), x, y, work_pid, allow)


def check_keyboard(windows: list[Win], region: Region, work_pid: int, frontmost: int | None,
                   allow: frozenset[str] = frozenset(), focus_note: str = "") -> str | None:
    """Keystrokes need BOTH: the work browser has focus AND nothing foreign is in front of the work area
    (a Spotlight or Raycast panel takes focus without being the 'frontmost application' in every case, and
    a notification can sit over the page). Pure: the caller passes the window list and the frontmost pid."""
    if frontmost != work_pid:
        return f"the focused app is {focus_note or 'unknown'} (pid {frontmost}), not the work browser"
    return check_region(windows, Region(region.x, region.y, region.width, region.height), work_pid, allow)


def guard_keyboard(work_pid: int, region: Region, allow: frozenset[str] | None = None) -> str | None:
    allow = allowed_overlays_from_env() if allow is None else allow
    focus = check_frontmost(work_pid)
    if focus is not None:
        return focus
    return check_keyboard(list_windows(), region, work_pid, work_pid, allow)


def bring_to_front(pid: int):
    """Best effort. A slow or failing System Events is not an error here: the caller re-runs the guard
    check afterwards, and that check, not this call, decides whether the agent may continue."""
    try:
        subprocess.run(["osascript", "-e", f'tell application "System Events" to set frontmost of (first process whose unix id is {int(pid)}) to true'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


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


def click(x: float, y: float, before_press=None):
    """Glide to the point, then click, so a viewer can follow the cursor. before_press() runs right before
    the mouse goes down and may raise to abort."""
    start = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    for i in range(1, 16):
        t = i / 15
        t = t * t * (3 - 2 * t)
        _mouse(Quartz.kCGEventMouseMoved, (start.x + (x - start.x) * t, start.y + (y - start.y) * t))
        time.sleep(0.012)
    time.sleep(0.15)
    if before_press:
        before_press()
    _mouse(Quartz.kCGEventLeftMouseDown, (x, y))
    time.sleep(0.06)
    _mouse(Quartz.kCGEventLeftMouseUp, (x, y))


def type_text(text: str, before_each=None):
    for character in text:
        if before_each:
            before_each()
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(character.encode("utf-16-le")) // 2, character)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.025)


KEYCODES = {"enter": 36, "return": 36, "tab": 48, "escape": 53, "space": 49, "backspace": 51, "delete": 117,
            "up": 126, "down": 125, "left": 123, "right": 124, "pagedown": 121, "pageup": 116, "a": 0}
MODIFIERS = {"cmd": Quartz.kCGEventFlagMaskCommand, "shift": Quartz.kCGEventFlagMaskShift,
             "alt": Quartz.kCGEventFlagMaskAlternate, "ctrl": Quartz.kCGEventFlagMaskControl}


def press(key: str, before=None):
    """Press an allowlisted key (agent/screen_keys.py): enter, tab, arrows, ... or cmd+a. Nothing else."""
    reason = check_key_allowed(key)
    if reason:
        raise ValueError(reason)
    if before:
        before()
    *modifiers, name = normalize_key(key).split("+")
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
