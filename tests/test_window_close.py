"""M3 (2026-10-12 audit): closing the dashboard must hide it, not quit the
app and take push-to-talk down with it. Quitting must still quit."""
from voisr.ui import close_hides_window

LEFT_MOUSE_DOWN, LEFT_MOUSE_UP, KEY_DOWN = 1, 2, 10
CMD = 1 << 20
NOW = 5000.0   # system uptime, the clock NSEvent timestamps use


class FakeEvent:
    def __init__(self, kind, window=None, chars="", flags=0, timestamp=NOW):
        self._kind, self._window, self._chars, self._flags = kind, window, chars, flags
        self._timestamp = timestamp

    def timestamp(self):
        return self._timestamp

    def type(self):
        return self._kind

    def window(self):
        return self._window

    def charactersIgnoringModifiers(self):
        return self._chars

    def modifierFlags(self):
        return self._flags


DASHBOARD = object()
OTHER_WINDOW = object()


def test_the_red_close_button_hides():
    assert close_hides_window(FakeEvent(LEFT_MOUSE_UP, DASHBOARD), DASHBOARD, NOW)
    assert close_hides_window(FakeEvent(LEFT_MOUSE_DOWN, DASHBOARD), DASHBOARD, NOW)


def test_cmd_w_hides():
    assert close_hides_window(FakeEvent(KEY_DOWN, DASHBOARD, "w", CMD), DASHBOARD, NOW)


def test_quit_from_the_app_menu_or_cmd_q_still_quits():
    assert not close_hides_window(FakeEvent(KEY_DOWN, DASHBOARD, "q", CMD), DASHBOARD, NOW)


def test_a_quit_with_no_ui_event_still_quits():
    # Dock "Quit", logout and shutdown arrive as Apple Events: there is no
    # click or key to point at, and refusing them would block logout.
    assert not close_hides_window(None, DASHBOARD, NOW)


def test_a_click_in_another_window_does_not_count():
    # the menu-bar menu's Quit is a click, but not in the dashboard
    assert not close_hides_window(FakeEvent(LEFT_MOUSE_UP, OTHER_WINDOW), DASHBOARD, NOW)


def test_a_broken_event_never_blocks_quitting():
    class Broken:
        def type(self):
            raise RuntimeError("pyobjc bridge gone")

    assert not close_hides_window(Broken(), DASHBOARD, NOW)


def test_a_stale_click_does_not_turn_a_dock_quit_into_a_hide():
    # an Apple Event quit doesn't replace currentEvent: the last thing the app
    # took may be a click in the dashboard from minutes ago
    old_click = FakeEvent(LEFT_MOUSE_UP, DASHBOARD, timestamp=NOW - 120)
    assert not close_hides_window(old_click, DASHBOARD, NOW)
