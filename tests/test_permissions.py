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


@pytest.mark.parametrize("status, expected", [
    (0, None),    # not determined: macOS asks on the first recording
    (1, False),   # restricted
    (2, False),   # denied
    (3, True),    # authorized
])
def test_microphone_not_yet_asked_is_unknown_not_denied(status, expected):
    # H2 (2026-10-12 audit): a fresh install has never been asked for the mic.
    # Reporting that as "off" sent new users to a pane Voisr isn't listed in.
    assert permissions.microphone_state(status) is expected


def test_a_probe_answering_none_stays_unknown():
    out = permissions.states(probes={
        "microphone": lambda: None,
        "input_monitoring": lambda: True,
        "accessibility": lambda: True,
    })
    assert out[0]["granted"] is None


ALL_TRUE = {"microphone": lambda: True, "input_monitoring": lambda: True,
            "accessibility": lambda: True}


def test_listen_mode_lists_three_panes_and_marks_input_monitoring_as_the_hotkey():
    out = permissions.states(probes=ALL_TRUE, mode="listen")
    assert [p["key"] for p in out] == ["microphone", "input_monitoring", "accessibility"]
    assert [p["key"] for p in out if p["hotkey"]] == ["input_monitoring"]
    assert all(p["effective"] is True for p in out)


def test_active_mode_drops_input_monitoring_and_marks_accessibility_as_the_hotkey():
    out = permissions.states(probes=ALL_TRUE, mode="active")
    assert [p["key"] for p in out] == ["microphone", "accessibility"]
    assert [p["key"] for p in out if p["hotkey"]] == ["accessibility"]


def test_hotkey_key_per_mode():
    assert permissions.hotkey_key("listen") == "input_monitoring"
    assert permissions.hotkey_key("active") == "accessibility"


def test_reset_runs_tccutil_for_voisrs_own_bundle_only():
    ran = []
    result = permissions.reset_grant("accessibility", bundle_id="com.local.voisr",
                                     runner=lambda argv: ran.append(argv) or 0)
    assert result == {"ok": True}
    assert ran == [["/usr/bin/tccutil", "reset", "Accessibility", "com.local.voisr"]]


@pytest.mark.parametrize("bundle_id", [None, "org.python.python", "com.apple.Terminal"])
def test_reset_refuses_when_not_running_as_voisr_app(bundle_id):
    # `python -m voisr.ui` must never reset Python's or Terminal's grants
    ran = []
    result = permissions.reset_grant("accessibility", bundle_id=bundle_id or "",
                                     runner=lambda argv: ran.append(argv) or 0)
    assert result["ok"] is False and ran == []


def test_reset_rejects_unknown_keys():
    ran = []
    assert permissions.reset_grant("camera", bundle_id="com.local.voisr",
                                   runner=lambda a: ran.append(a) or 0)["ok"] is False
    assert ran == []


def test_reset_reports_a_failing_tccutil():
    result = permissions.reset_grant("input_monitoring", bundle_id="com.local.voisr",
                                     runner=lambda argv: 1)
    assert result["ok"] is False and "1" in result["error"]


def test_reset_reports_a_runner_that_raises():
    def boom(argv):
        raise OSError("no tccutil")
    result = permissions.reset_grant("microphone", bundle_id="com.local.voisr",
                                     runner=boom)
    assert result["ok"] is False and "no tccutil" in result["error"]


def test_input_monitoring_maps_to_listen_event():
    assert permissions.TCC_SERVICES["input_monitoring"] == "ListenEvent"
