"""Optionally open a start URL in a work-area browser window that this process owns, and close it afterwards."""
import glob
import os
import subprocess
import tempfile
import time

PLAYWRIGHT_GLOB = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def find_browser() -> str | None:
    if os.environ.get("BROWSER") and os.path.exists(os.environ["BROWSER"]):
        return os.environ["BROWSER"]
    if os.path.exists(CHROME):
        return CHROME
    found = sorted(glob.glob(PLAYWRIGHT_GLOB))
    return found[-1] if found else None


class WorkBrowser:
    def __init__(self, url: str, width: int = 1400, height: int = 1080):
        binary = find_browser()
        if not binary:
            raise RuntimeError("no Chromium-family browser found; set BROWSER to one")
        self._profile = tempfile.TemporaryDirectory()
        self._proc = subprocess.Popen(
            [binary, f"--user-data-dir={self._profile.name}", "--no-first-run", "--use-mock-keychain", "--password-store=basic", "--no-default-browser-check",
             "--window-position=0,0", f"--window-size={width},{height}", f"--app={url}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @property
    def pid(self) -> int:
        """The browser's main process: the owner of its windows in CGWindowList."""
        return self._proc.pid

    def wait_ready(self, seconds: float = 2.5):
        time.sleep(seconds)
        # Bring our own window to the front: macOS does not always focus a process started from a terminal.
        subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of '
                        f"(first process whose unix id is {self._proc.pid}) to true"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.7)

    def close(self):
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._profile.cleanup()


def _ps_args(pid: int) -> str:
    out = subprocess.run(["ps", "-o", "args=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5)
    return out.stdout.strip()


def check_work_pid(pid: int, profile_dir: str, ps_args=_ps_args) -> str | None:
    """None if `pid` is a browser that THIS run started, else the reason.

    Why: --work-pid tells the agent which window it may capture and click. Without this check it could be
    pointed at the user's own, logged-in browser. run.sh creates a fresh temp profile directory for each run
    (`.../private-agent.<random>/profile-work`) and starts the work browser with `--user-data-dir=<that dir>`;
    a real browser profile never has that path. We read the process's command line and require it.
    """
    if not profile_dir:
        return "--work-pid needs --work-profile: the temp profile directory the work browser was started with"
    real = os.path.realpath(profile_dir)
    parent = os.path.basename(os.path.dirname(real))
    if os.path.basename(real) != "profile-work" or not parent.startswith("private-agent."):
        return f"{profile_dir!r} is not a per-run profile directory created by run.sh (private-agent.<token>/profile-work)"
    if not os.path.isdir(real) or os.stat(real).st_uid != os.getuid():
        return f"{profile_dir!r} is not a directory owned by you"
    args = ps_args(pid)
    if not args:
        return f"no process with pid {pid}"
    wanted = {profile_dir, real}
    for part in args.split(" --"):
        part = part.strip().removeprefix("--")
        if part.startswith("user-data-dir="):
            value = part.split("=", 1)[1].strip()
            if value in wanted or os.path.realpath(value) == real:
                return None
    return f"pid {pid} was not started with --user-data-dir={profile_dir}: it is not this run's work browser"
