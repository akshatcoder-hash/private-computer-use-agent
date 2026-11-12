"""Keystroke guard (focus AND region), key allowlist, --work-pid check, screenshot file mode. No real screen."""
import os
import stat
import tempfile
import unittest
from unittest import mock

from agent import loop, screen
from agent.browser import check_work_pid
from agent.planner import ACTION_SCHEMA, SYSTEM_PROMPT, Planner
from agent.receipts import Run
from agent.screen import Region, Win, check_keyboard
from agent.screen_keys import ALLOWED_KEYS, check_key_allowed

REGION = Region(0, 30, 1400, 1050)
ME = 100


def win(pid, owner, x, y, w, h, layer=0, name="", alpha=1.0):
    return Win(pid, owner, name, layer, alpha, x, y, w, h)


MENUBAR = win(1, "Window Server", 0, 0, 1920, 30, layer=24, name="Menubar")
MINE = win(ME, "Chrome", 0, 30, 1400, 1050)
SPOTLIGHT = win(500, "Spotlight", 400, 200, 700, 80, layer=102)


class KeyboardGuard(unittest.TestCase):
    def test_focus_and_clear_region_passes(self):
        self.assertIsNone(check_keyboard([MENUBAR, MINE], REGION, ME, ME))

    def test_wrong_frontmost_app_blocks(self):
        self.assertIn("not the work browser", check_keyboard([MENUBAR, MINE], REGION, ME, 999, focus_note="Notes"))

    def test_spotlight_panel_over_region_blocks_even_if_work_app_is_frontmost(self):
        reason = check_keyboard([SPOTLIGHT, MENUBAR, MINE], REGION, ME, ME)
        self.assertIn("Spotlight", reason)

    def test_notification_over_region_blocks(self):
        banner = win(5, "Notification Center", 1000, 40, 360, 90, layer=23)
        self.assertIn("Notification Center", check_keyboard([banner, MENUBAR, MINE], REGION, ME, ME))

    def test_guard_check_keys_needs_both(self):
        guard = loop.Guard(ME, REGION)
        with mock.patch.object(screen, "list_windows", return_value=[SPOTLIGHT, MENUBAR, MINE]), \
                mock.patch.object(screen, "check_frontmost", return_value=None):
            self.assertIn("Spotlight", guard.check_keys())
        with mock.patch.object(screen, "list_windows", return_value=[MENUBAR, MINE]), \
                mock.patch.object(screen, "check_frontmost", return_value="the focused app is X"):
            self.assertIn("focused app", guard.check_keys())
        with mock.patch.object(screen, "list_windows", return_value=[MENUBAR, MINE]), \
                mock.patch.object(screen, "check_frontmost", return_value=None):
            self.assertIsNone(guard.check_keys())

    def test_type_and_key_run_the_full_check_before_every_event(self):
        guard = loop.Guard(ME, REGION)
        calls = []
        with mock.patch.object(guard, "check_keys", side_effect=lambda: calls.append(1)), \
                mock.patch.object(screen, "type_text", side_effect=lambda t, before_each=None: [before_each() for _ in t]), \
                mock.patch.object(screen, "press", side_effect=lambda k, before=None: before()):
            loop._act({"action": "type", "text": "ab", "enter": True}, b"", 1, mock.Mock(), REGION, guard)
            n_type = len(calls)
            loop._act({"action": "key", "key": "tab"}, b"", 1, mock.Mock(), REGION, guard)
        self.assertEqual(n_type, 1 + 2 + 1)      # pre-check, one per character, one for enter
        self.assertEqual(len(calls) - n_type, 2)  # pre-check and the posted event

    def test_blocked_keys_never_post(self):
        guard = loop.Guard(ME, REGION)
        with mock.patch.object(guard, "check_keys", return_value="covered by Spotlight"), \
                mock.patch.object(guard, "passes", side_effect=loop.GuardBlocked("type", "covered by Spotlight")), \
                mock.patch.object(screen, "type_text") as typed:
            with self.assertRaises(loop.GuardBlocked):
                loop._act({"action": "type", "text": "x"}, b"", 1, mock.Mock(), REGION, guard)
            typed.assert_not_called()


class KeyAllowlist(unittest.TestCase):
    def test_allowed(self):
        for key in ("enter", "Return", "tab", "escape", "backspace", "delete", "up", "down", "left", "right",
                    "pageup", "pagedown", "space", "cmd+a", "CMD+A", "cmd + a"):
            self.assertIsNone(check_key_allowed(key), key)

    def test_refused(self):
        for key in ("cmd+space", "cmd+tab", "cmd+q", "cmd+w", "cmd+c", "cmd+v", "cmd+l", "cmd+t", "ctrl+a", "ctrl+c", "ctrl+space",
                    "alt+tab", "shift+tab", "cmd+shift+a", "f1", "a", "", "cmd+a+b", "fn", "home"):
            self.assertIsNotNone(check_key_allowed(key), key)

    def test_press_refuses_before_posting(self):
        for key in ("cmd+space", "cmd+q", "ctrl+left"):
            with mock.patch.object(screen.Quartz, "CGEventPost") as post, self.assertRaises(ValueError):
                screen.press(key, before=lambda: self.fail("guard must not even run"))
            post.assert_not_called()

    def test_loop_refuses_disallowed_key_without_pressing(self):
        guard = loop.Guard(ME, REGION)
        with mock.patch.object(screen, "press") as pressed:
            result = loop._act({"action": "key", "key": "cmd+space"}, b"", 1, mock.Mock(), REGION, guard)
        pressed.assert_not_called()
        self.assertTrue(result.startswith("FAILED"))

    def test_prompt_and_schema_state_the_allowlist(self):
        for key in ALLOWED_KEYS:
            self.assertIn(key, SYSTEM_PROMPT)
        self.assertEqual(ACTION_SCHEMA["json_schema"]["schema"]["properties"]["key"]["enum"], list(ALLOWED_KEYS))
        for banned in ("cmd+space", "cmd+tab", "cmd+q"):
            self.assertNotIn(banned, ACTION_SCHEMA["json_schema"]["schema"]["properties"]["key"]["enum"])


class NoPlaintextPath(unittest.TestCase):
    def test_planner_requires_e2ee(self):
        with self.assertRaises(ValueError):
            Planner("k", e2ee=False)

    def test_cli_has_no_no_e2ee_flag(self):
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-m", "agent", "run", "t", "--no-e2ee"], cwd=root, capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("unrecognized arguments", out.stderr)

    def test_kept_screenshots_are_owner_only(self):
        with tempfile.TemporaryDirectory() as root:
            run = Run(root, "r")
            run.write_screenshot(1, b"\x89PNG")
            mode = stat.S_IMODE(os.stat(os.path.join(run.dir, "step-1.png")).st_mode)
            self.assertEqual(mode, 0o600)


class WorkPid(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="x")
        base = os.path.join(self.tmp.name, "private-agent.AbC123")
        self.profile = os.path.join(base, "profile-work")
        os.makedirs(self.profile)
        self.good = f'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir={self.profile} --use-mock-keychain --app=http://x/'

    def tearDown(self):
        self.tmp.cleanup()

    def test_browser_started_with_the_run_profile_is_accepted(self):
        self.assertIsNone(check_work_pid(5, self.profile, ps_args=lambda pid: self.good))

    def test_users_own_browser_is_refused(self):
        own = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        self.assertIn("not this run's work browser", check_work_pid(5, self.profile, ps_args=lambda pid: own))
        other = own + " --user-data-dir=/Users/me/Library/Application Support/Google/Chrome"
        self.assertIn("not this run's work browser", check_work_pid(5, self.profile, ps_args=lambda pid: other))

    def test_prefix_of_profile_path_is_not_enough(self):
        args = f"Chrome --user-data-dir={self.profile}-evil --app=x"
        self.assertIsNotNone(check_work_pid(5, self.profile, ps_args=lambda pid: args))

    def test_profile_argument_required_and_must_look_like_runs(self):
        self.assertIn("needs --work-profile", check_work_pid(5, "", ps_args=lambda pid: self.good))
        real = os.path.expanduser("~/Library/Application Support/Google/Chrome")
        self.assertIn("not a per-run profile", check_work_pid(5, real, ps_args=lambda pid: f"Chrome --user-data-dir={real}"))

    def test_no_such_process(self):
        self.assertIn("no process", check_work_pid(5, self.profile, ps_args=lambda pid: ""))

    def test_symlinked_tmpdir_still_matches(self):
        link = os.path.join(self.tmp.name, "link")
        os.symlink(os.path.join(self.tmp.name, "private-agent.AbC123"), link)
        args = self.good.replace(self.profile, os.path.realpath(self.profile))
        self.assertIsNone(check_work_pid(5, os.path.join(link, "profile-work"), ps_args=lambda pid: args))


if __name__ == "__main__":
    unittest.main()
