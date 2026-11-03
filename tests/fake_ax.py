"""A scriptable stand-in for voisr.ax_context.MacAXAdapter."""
from voisr.ax_context import UNREADABLE


class FakeElement:
    def __init__(self, **attrs):
        self.attrs = attrs

    @property
    def id(self):
        return f"el-{id(self)}"


def textbox(value="", caret=None, selection=0, role="AXTextArea", **attrs):
    caret = len(value) if caret is None else caret
    return FakeElement(AXRole=role, AXValue=value,
                       AXSelectedTextRange=(caret, selection),
                       AXNumberOfCharacters=len(value), **attrs)


class FakeAX:
    """Every call is logged in .calls as (element, attribute)."""

    def __init__(self, focused=None, bundle_id="com.example.app", name="Example",
                 pid=4242, electron=False):
        self.focused = focused
        self.bundle_id = bundle_id
        self.name = name
        self.pid = pid
        self.electron = electron
        self.owner_bundle_id = None
        self.calls = []
        self.front_calls = 0

    def text_reads(self):
        return [a for _, a in self.calls if a in ("AXValue", "AXSelectedText",
                                                  "AXSelectedTextRange", "AXTitle")]

    def frontmost(self):
        self.front_calls += 1
        return self.bundle_id, self.name, self.pid

    def focused_element(self, timeout_s):
        self.calls.append((None, "AXFocusedUIElement"))
        return self.focused if self.focused is not None else UNREADABLE

    def attribute(self, element, name, timeout_s):
        self.calls.append((element, name))
        if name not in element.attrs:
            return UNREADABLE
        value = element.attrs[name]
        return value() if callable(value) else value

    def owner(self, element):
        return self.owner_bundle_id or self.bundle_id, self.name, self.pid

    def element_id(self, element):
        return element.id

    def is_electron(self, pid):
        return self.electron
