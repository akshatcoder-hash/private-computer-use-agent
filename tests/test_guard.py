"""Window-geometry guard on synthetic window lists (front to back). No real screen."""
import unittest

from agent.screen import Region, Win, check_point
from agent.screen import check_region as _check_region

REGION = Region(0, 0, 1400, 1080)
ME = 100


def win(pid, owner, x, y, w, h, layer=0, name="", alpha=1.0):
    return Win(pid, owner, name, layer, alpha, x, y, w, h)


MENUBAR = win(1, "Window Server", 0, 0, 1920, 30, layer=24, name="Menubar")
CURSOR = win(1, "Window Server", 1896, 1, 28, 28, layer=2147483630, name="StatusIndicator")
DOCK = win(2, "Dock", 0, 1000, 1400, 80, layer=20)
MINE = win(ME, "Chrome", 0, 30, 1400, 1050)          # macOS keeps windows below the menu bar
PANEL = win(200, "Chrome", 1400, 30, 520, 1050)       # the panel: another instance, right of the region
OTHER = win(300, "ChatGPT", 100, 100, 800, 600)


def check_region(windows, region, pid, allow=frozenset(), menubar=True):
    return _check_region(windows + ([MENUBAR] if menubar and MENUBAR not in windows else []), region, pid, allow)


class ClipTests(unittest.TestCase):
    def test_region_starts_below_menu_bar(self):
        from agent.screen import clip_below_menubar
        self.assertEqual(clip_below_menubar([MENUBAR, MINE], REGION), Region(0, 30, 1400, 1050))

    def test_no_menu_bar_leaves_region(self):
        from agent.screen import clip_below_menubar
        self.assertEqual(clip_below_menubar([MINE], REGION), REGION)


class RegionTests(unittest.TestCase):
    def test_covered_by_own_window_is_ok(self):
        self.assertIsNone(check_region([MINE, OTHER], REGION, ME))

    def test_foreign_window_in_front_blocks_and_is_named(self):
        reason = check_region([OTHER, MINE], REGION, ME)
        self.assertIn("ChatGPT", reason)

    def test_foreign_window_behind_is_fine(self):
        self.assertIsNone(check_region([MINE, OTHER, PANEL], REGION, ME))

    def test_own_window_not_covering_blocks(self):
        small = win(ME, "Chrome", 0, 30, 800, 600)
        self.assertIn("does not cover", check_region([small], REGION, ME))
        self.assertIsNotNone(check_region([small, OTHER], REGION, ME))

    def test_no_window_of_ours_blocks(self):
        self.assertIn("no on-screen window", check_region([PANEL], REGION, ME))

    def test_menu_bar_cursor_and_dock_ignored(self):
        self.assertIsNone(check_region([CURSOR, MENUBAR, DOCK, MINE], REGION, ME))

    def test_menu_bar_status_item_ignored(self):
        item = win(3, "Control Center", 1300, 0, 40, 30, layer=25, name="Item-0")
        self.assertIsNone(check_region([item, MENUBAR, MINE], REGION, ME))

    def test_without_menu_bar_window_must_cover_to_top(self):
        self.assertIn("does not cover", check_region([MINE], REGION, ME, menubar=False))

    def test_panel_at_x1400_does_not_intersect(self):
        self.assertIsNone(check_region([PANEL, MINE], REGION, ME))
        self.assertIsNone(check_region([MINE, PANEL], REGION, ME))

    def test_panel_overlapping_region_blocks(self):
        wide = win(200, "Chrome", 1300, 30, 520, 1050)
        self.assertIn("Chrome", check_region([wide, MINE], REGION, ME))

    def test_high_layer_foreign_overlay_blocks_unless_allowed(self):
        overlay = win(400, "Wispr Flow", 704, 466, 512, 614, layer=1000, name="Status")
        self.assertIn("Wispr Flow", check_region([overlay, MINE], REGION, ME))
        self.assertIsNone(check_region([overlay, MINE], REGION, ME, frozenset({"Wispr Flow"})))

    def test_notification_banner_over_region_blocks(self):
        banner = win(5, "Notification Center", 1000, 40, 360, 90, layer=23)
        self.assertIn("Notification Center", check_region([banner, MINE], REGION, ME))

    def test_transparent_window_ignored(self):
        self.assertIsNone(check_region([win(300, "ChatGPT", 0, 0, 1920, 1080, alpha=0.0), MINE], REGION, ME))

    def test_own_popup_in_front_is_fine(self):
        self.assertIsNone(check_region([win(ME, "Chrome", 500, 200, 200, 300, layer=101), MINE], REGION, ME))

    def test_reason_lists_several_owners(self):
        reason = check_region([OTHER, win(301, "Notes", 50, 50, 300, 300), MINE], REGION, ME)
        self.assertIn("ChatGPT", reason)
        self.assertIn("Notes", reason)


class PointTests(unittest.TestCase):
    def test_own_window_topmost_ok(self):
        self.assertIsNone(check_point([MINE, OTHER], 500, 400, ME))

    def test_covered_by_other_app_blocks(self):
        self.assertIn("ChatGPT", check_point([OTHER, MINE], 500, 400, ME))

    def test_point_outside_other_window_ok(self):
        self.assertIsNone(check_point([OTHER, MINE], 1200, 900, ME))

    def test_dock_and_menu_bar_block_a_click(self):
        self.assertIn("Dock", check_point([DOCK, MINE], 500, 1040, ME))
        self.assertIn("Menubar", check_point([MENUBAR, MINE], 500, 10, ME) or "")

    def test_cursor_ignored(self):
        self.assertIsNone(check_point([win(1, 'Window Server', 400, 300, 200, 200, layer=2147483630), MINE], 500, 400, ME))

    def test_no_window_blocks(self):
        self.assertIn("no window", check_point([PANEL], 500, 400, ME))


if __name__ == "__main__":
    unittest.main()
