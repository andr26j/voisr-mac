import subprocess

import pytest

import voisr.inject as inject
from voisr.pasteboard import (
    ClipboardPreservationError,
    PasteDeliveryError,
    PasteboardSnapshot,
)


def test_plain_text_clipboard_commands_are_bounded_and_checked(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=b"saved")

    monkeypatch.setattr(inject.subprocess, "run", run)

    inject.set_clipboard("new")
    assert inject.get_clipboard() == "saved"
    assert calls == [
        (
            ["pbcopy"],
            {
                "input": b"new",
                "check": True,
                "timeout": inject.COMMAND_TIMEOUT_S,
            },
        ),
        (
            ["pbpaste"],
            {
                "capture_output": True,
                "check": True,
                "timeout": inject.COMMAND_TIMEOUT_S,
            },
        ),
    ]


@pytest.mark.parametrize("command", ["pbcopy", "pbpaste", "osascript"])
def test_clipboard_command_timeout_propagates(monkeypatch, command):
    def timeout(args, **kwargs):
        if args[0] == command:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(inject.subprocess, "run", timeout)

    with pytest.raises(subprocess.TimeoutExpired):
        if command == "pbcopy":
            inject.set_clipboard("text")
        elif command == "pbpaste":
            inject.get_clipboard()
        else:
            inject._applescript_cmd_v()


class FakeAdapter:
    def __init__(self):
        self.snapshot_value = PasteboardSnapshot(4, ())
        self.owner_count = 6
        self.restore_result = True
        self.calls = []
        self.snapshot_error = None
        self.restore_error = None

    def snapshot(self):
        self.calls.append("snapshot")
        if self.snapshot_error:
            raise self.snapshot_error
        return self.snapshot_value

    def write_text_if_unchanged(self, text, snapshot):
        self.calls.append(("write", text, snapshot))
        return self.owner_count

    def restore_if_unchanged(self, snapshot, owner_count):
        self.calls.append(("restore", snapshot, owner_count))
        if self.restore_error:
            raise self.restore_error
        return self.restore_result


def test_successful_paste_restores_after_grace():
    adapter = FakeAdapter()
    slept = []

    assert inject._paste_with_adapter(
        "hello",
        adapter=adapter,
        post_cmd_v=lambda: True,
        applescript_cmd_v=lambda: False,
        sleeper=slept.append,
    ) is True

    assert slept == [inject.PASTE_GRACE_S]
    assert adapter.calls[-1] == (
        "restore", adapter.snapshot_value, adapter.owner_count)


def test_external_change_skips_restore_without_failing_paste():
    adapter = FakeAdapter()
    adapter.restore_result = False

    assert inject._paste_with_adapter(
        "hello", adapter=adapter, post_cmd_v=lambda: True,
        applescript_cmd_v=lambda: False, sleeper=lambda delay: None,
    ) is True


def test_blocked_paste_restores_then_raises_without_safe_fallback():
    adapter = FakeAdapter()

    with pytest.raises(PasteDeliveryError) as caught:
        inject._paste_with_adapter(
            "hello", adapter=adapter, post_cmd_v=lambda: False,
            applescript_cmd_v=lambda: False, sleeper=lambda delay: None,
        )

    assert caught.value.pasted is False
    assert caught.value.fallback_safe is False
    assert adapter.calls[-1][0] == "restore"


def test_restore_failure_reports_that_paste_already_landed():
    adapter = FakeAdapter()
    adapter.restore_error = OSError("restore unavailable")

    with pytest.raises(PasteDeliveryError) as caught:
        inject._paste_with_adapter(
            "hello", adapter=adapter, post_cmd_v=lambda: True,
            applescript_cmd_v=lambda: False, sleeper=lambda delay: None,
        )

    assert caught.value.pasted is True
    assert caught.value.fallback_safe is False
    assert "restore" in str(caught.value)


def test_snapshot_failure_never_attempts_write_or_paste():
    adapter = FakeAdapter()
    adapter.snapshot_error = ClipboardPreservationError("unreadable type")
    pasted = []

    with pytest.raises(ClipboardPreservationError):
        inject._paste_with_adapter(
            "hello", adapter=adapter,
            post_cmd_v=lambda: pasted.append(True),
            applescript_cmd_v=lambda: False, sleeper=lambda delay: None,
        )

    assert adapter.calls == ["snapshot"]
    assert pasted == []


def test_restore_disabled_leaves_text_for_manual_paste(monkeypatch):
    copied = []
    monkeypatch.setattr(inject, "set_clipboard", copied.append)
    monkeypatch.setattr(inject, "_post_cmd_v", lambda: False)
    monkeypatch.setattr(inject, "_applescript_cmd_v", lambda: False)

    assert inject.paste_at_cursor("manual", restore_clipboard=False) is False
    assert copied == ["manual"]


def test_paste_command_exception_restores_before_reporting():
    adapter = FakeAdapter()

    with pytest.raises(PasteDeliveryError, match="command failed"):
        inject._paste_with_adapter(
            "hello", adapter=adapter,
            post_cmd_v=lambda: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["osascript"], 2)),
            applescript_cmd_v=lambda: False,
            sleeper=lambda delay: None,
        )

    assert adapter.calls[-1][0] == "restore"


def test_wait_exception_restores_then_reports_paste_landed():
    adapter = FakeAdapter()
    wait_error = RuntimeError("wait interrupted")

    with pytest.raises(PasteDeliveryError) as caught:
        inject._paste_with_adapter(
            "hello", adapter=adapter,
            post_cmd_v=lambda: True,
            applescript_cmd_v=lambda: False,
            sleeper=lambda delay: (_ for _ in ()).throw(wait_error),
        )

    assert caught.value.pasted is True
    assert caught.value.fallback_safe is False
    assert caught.value.__cause__ is wait_error
    assert adapter.calls[-1][0] == "restore"


def test_wait_and_restore_failures_both_report_paste_landed():
    adapter = FakeAdapter()
    adapter.restore_error = OSError("restore unavailable")
    wait_error = RuntimeError("wait interrupted")

    with pytest.raises(PasteDeliveryError) as caught:
        inject._paste_with_adapter(
            "hello", adapter=adapter,
            post_cmd_v=lambda: True,
            applescript_cmd_v=lambda: False,
            sleeper=lambda delay: (_ for _ in ()).throw(wait_error),
        )

    assert caught.value.pasted is True
    assert "wait interrupted" in str(caught.value)
    assert "restore unavailable" in str(caught.value)
    assert caught.value.__cause__ is wait_error
    assert adapter.calls[-1][0] == "restore"


def test_keyboard_interrupt_restores_then_reraises_original():
    adapter = FakeAdapter()
    interrupt = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt) as caught:
        inject._paste_with_adapter(
            "hello", adapter=adapter,
            post_cmd_v=lambda: True,
            applescript_cmd_v=lambda: False,
            sleeper=lambda delay: (_ for _ in ()).throw(interrupt),
        )

    assert caught.value is interrupt
    assert adapter.calls[-1][0] == "restore"


def test_keyboard_interrupt_chains_restore_failure_without_replacement():
    adapter = FakeAdapter()
    adapter.restore_error = OSError("restore unavailable")
    interrupt = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt) as caught:
        inject._paste_with_adapter(
            "hello", adapter=adapter,
            post_cmd_v=lambda: True,
            applescript_cmd_v=lambda: False,
            sleeper=lambda delay: (_ for _ in ()).throw(interrupt),
        )

    assert caught.value is interrupt
    assert str(caught.value.__cause__) == "restore unavailable"
    assert adapter.calls[-1][0] == "restore"
