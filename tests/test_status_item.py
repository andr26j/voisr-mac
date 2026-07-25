from voisr.status_item import StatusItem


class FakeButton:
    def __init__(self):
        self.title = None
        self.tooltip = None

    def setTitle_(self, t):
        self.title = t

    def setToolTip_(self, t):
        self.tooltip = t


class FakeItem:
    def __init__(self):
        self.button_obj = FakeButton()
        self.menu = None
        self.visible = True

    def button(self):
        return self.button_obj

    def setMenu_(self, m):
        self.menu = m

    def setVisible_(self, v):
        self.visible = v


class FakeStatusBar:
    def __init__(self):
        self.items = []
        self.removed = []

    def statusItemWithLength_(self, length):
        item = FakeItem()
        self.items.append(item)
        return item

    def removeStatusItem_(self, item):
        self.removed.append(item)


def make(**kw):
    bar = FakeStatusBar()
    si = StatusItem(on_dictate=kw.get("on_dictate", lambda: None),
                    on_open=kw.get("on_open", lambda: None),
                    on_quit=kw.get("on_quit", lambda: None),
                    hotkey_label_fn=kw.get("hotkey_label_fn", lambda: "Fn"),
                    status_bar=bar)
    return si, bar


def test_create_adds_one_status_item():
    si, bar = make()
    assert si.create() is True
    assert len(bar.items) == 1
    assert si.item is bar.items[0]


def test_create_is_idempotent():
    si, bar = make()
    si.create()
    si.create()
    assert len(bar.items) == 1


def test_idle_and_recording_states_differ():
    si, _ = make()
    si.create()
    idle = si.item.button().title
    si.set_recording(True)
    assert si.item.button().title != idle
    si.set_recording(False)
    assert si.item.button().title == idle


def test_tooltip_shows_the_hotkey_label():
    si, _ = make(hotkey_label_fn=lambda: "Right Option")
    si.create()
    assert "Right Option" in si.item.button().tooltip


def test_release_removes_the_item_and_is_idempotent():
    si, bar = make()
    si.create()
    si.release()
    si.release()
    assert len(bar.removed) == 1
    assert si.item is None


def test_set_recording_before_create_does_not_raise():
    si, _ = make()
    si.set_recording(True)  # must not raise


def test_dispatch_routes_each_menu_action_to_its_handler():
    calls = []
    si, _ = make(on_dictate=lambda: calls.append("dictate"),
                 on_open=lambda: calls.append("open"),
                 on_quit=lambda: calls.append("quit"))
    si._dispatch("dictate")
    si._dispatch("open")
    si._dispatch("quit")
    assert calls == ["dictate", "open", "quit"]


def test_dispatch_ignores_an_unknown_action_and_swallows_handler_errors():
    def boom():
        raise RuntimeError("handler blew up")

    si, _ = make(on_dictate=boom)
    si._dispatch("nonsense")   # must not raise
    si._dispatch("dictate")    # must not raise


def test_a_broken_status_bar_reports_failure_without_raising():
    class Broken:
        def statusItemWithLength_(self, _):
            raise RuntimeError("no window server")

    si = StatusItem(on_dictate=lambda: None, on_open=lambda: None,
                    on_quit=lambda: None, hotkey_label_fn=lambda: "Fn",
                    status_bar=Broken())
    assert si.create() is False
    assert si.item is None
