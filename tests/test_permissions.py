import pytest

from voisr import permissions


def test_states_returns_three_panes_in_fixed_order():
    out = permissions.states(probes={
        "microphone": lambda: True,
        "input_monitoring": lambda: True,
        "accessibility": lambda: True,
    })
    assert [p["key"] for p in out] == [
        "microphone", "input_monitoring", "accessibility"]
    assert all(p["granted"] is True for p in out)
    assert all(p["settings_url"].startswith("x-apple.systempreferences:")
               for p in out)
    assert all(p["label"] for p in out)


def test_states_reports_denied():
    out = permissions.states(probes={
        "microphone": lambda: False,
        "input_monitoring": lambda: False,
        "accessibility": lambda: False,
    })
    assert all(p["granted"] is False for p in out)


def test_a_raising_probe_reports_unknown_not_granted():
    def boom():
        raise RuntimeError("no pyobjc here")

    out = permissions.states(probes={
        "microphone": boom,
        "input_monitoring": lambda: True,
        "accessibility": lambda: True,
    })
    by_key = {p["key"]: p for p in out}
    assert by_key["microphone"]["granted"] is None
    assert by_key["input_monitoring"]["granted"] is True


def test_settings_url_rejects_unknown_key():
    assert permissions.settings_url("microphone").endswith("Privacy_Microphone")
    assert permissions.settings_url("nonsense") is None


def test_open_pane_opens_the_mapped_url():
    opened = []
    assert permissions.open_pane("accessibility", opener=opened.append) is True
    assert opened == [permissions.settings_url("accessibility")]


def test_open_pane_refuses_unknown_key_without_calling_opener():
    opened = []
    assert permissions.open_pane("../../evil", opener=opened.append) is False
    assert opened == []
