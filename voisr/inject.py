import subprocess
import time

from voisr.pasteboard import (
    AppKitPasteboard,
    ClipboardPreservationError,
    PasteDeliveryError,
)


COMMAND_TIMEOUT_S = 2.0
# Cmd-V is posted asynchronously and nothing reports when the target app has
# read the clipboard, so the restore waits. 300 ms was shorter than a busy
# Electron or chat app's stall, which then pasted the restored clipboard
# instead. restore_if_unchanged still won't clobber anything copied meanwhile.
PASTE_GRACE_S = 1.0


def set_clipboard(text: str) -> None:
    subprocess.run(
        ["pbcopy"], input=text.encode("utf-8"), check=True,
        timeout=COMMAND_TIMEOUT_S,
    )


def get_clipboard() -> str:
    return subprocess.run(
        ["pbpaste"], capture_output=True, check=True,
        timeout=COMMAND_TIMEOUT_S,
    ).stdout.decode("utf-8")


_controller = None


def prepare_paste_controller() -> None:
    """Build the pynput Controller ON THE MAIN THREAD at app startup.

    Controller() reads the keyboard layout via TIS (Text Input Services),
    which is main-thread-only inside GUI apps — constructing it from a
    worker thread kills the process outright. Call this from main() before
    the UI loop starts; paste threads then reuse the prepared instance."""
    global _controller
    try:
        from pynput.keyboard import Controller
        _controller = Controller()
    except Exception:
        _controller = None


def _post_cmd_v() -> bool:
    """Send Cmd-V as CGEvents (needs Accessibility). Preflight-gated so we
    never claim success while events would silently go nowhere."""
    try:
        import Quartz
        if not Quartz.CGPreflightPostEventAccess():
            return False
        from pynput.keyboard import Controller, Key
        # Only construct here outside GUI apps (tests/CLI on the main thread)
        kb = _controller or Controller()
        with kb.pressed(Key.cmd):
            kb.tap("v")
        return True
    except Exception:
        return False


def _applescript_cmd_v() -> bool:
    proc = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke "v" using command down'],
        capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S)
    return proc.returncode == 0


def _restore_after_failure(adapter, snapshot, owner_count, error, *,
                           message, pasted):
    try:
        adapter.restore_if_unchanged(snapshot, owner_count)
    except Exception as restore_error:
        if isinstance(error, Exception):
            raise PasteDeliveryError(
                f"{message} and clipboard restore failed: {error}; "
                f"{restore_error}",
                pasted=pasted,
            ) from error
        raise error from restore_error
    if isinstance(error, Exception):
        raise PasteDeliveryError(
            f"{message}: {error}", pasted=pasted) from error
    raise error


def _restore_after_ownership_check_failure(adapter, snapshot, owner_count,
                                           error):
    try:
        adapter.restore_if_unchanged(snapshot, owner_count)
    except BaseException as restore_error:
        if not isinstance(error, Exception):
            raise error from restore_error
        if not isinstance(restore_error, Exception):
            raise restore_error from error
        raise ClipboardPreservationError(
            "clipboard ownership check and restore failed") from error
    if isinstance(error, ClipboardPreservationError):
        raise error
    if isinstance(error, Exception):
        raise ClipboardPreservationError(
            "clipboard ownership check failed") from error
    raise error


def _old_clipboard_text(snapshot) -> str | None:
    """What the clipboard held as text, to recognise it landing instead."""
    for item in snapshot.items:
        for type_name, payload in item.payloads:
            if type_name == "public.utf8-plain-text":
                return payload.decode("utf-8", "replace")
    return None


def _missed(verifier, snapshot) -> bool:
    """Ask the verifier whether the paste missed. Its failure means no: that
    is today's behaviour, and the clipboard goes back."""
    try:
        return bool(verifier(_old_clipboard_text(snapshot)))
    except Exception as error:
        print(f"Voisr: paste check skipped ({error.__class__.__name__})", flush=True)
        return False


def _paste_with_adapter(text, *, adapter, post_cmd_v,
                        applescript_cmd_v, sleeper, on_sent=None,
                        verifier=None) -> bool:
    snapshot = adapter.snapshot()
    owner_count = adapter.write_text_if_unchanged(text, snapshot)
    try:
        owned = adapter.is_owned(owner_count)
    except BaseException as error:
        _restore_after_ownership_check_failure(
            adapter, snapshot, owner_count, error)
    if not owned:
        try:
            adapter.restore_if_unchanged(snapshot, owner_count)
        except Exception as error:
            raise PasteDeliveryError(
                f"clipboard ownership lost and restore failed: {error}",
                pasted=False,
            ) from error
        raise PasteDeliveryError(
            "clipboard ownership lost before paste", pasted=False)
    try:
        pasted = post_cmd_v() or applescript_cmd_v()
    except BaseException as error:
        _restore_after_failure(
            adapter, snapshot, owner_count, error,
            message="paste command failed", pasted=False)
    if not pasted:
        try:
            adapter.restore_if_unchanged(snapshot, owner_count)
        except Exception as restore_error:
            raise PasteDeliveryError(
                f"paste was blocked and clipboard restore failed: "
                f"{restore_error}",
                pasted=False,
            ) from restore_error
        raise PasteDeliveryError(
            "paste was blocked; clipboard preserved", pasted=False)
    if on_sent is not None:
        on_sent()  # Cmd-V is out; only the clipboard-restore wait remains
    if snapshot.concealed:
        # A late Cmd-V would paste whatever is back on the clipboard; a
        # password must never be that. Leaving the dictation there instead
        # costs nothing a password manager wouldn't clear anyway.
        return True
    try:
        sleeper(PASTE_GRACE_S)
        # Read-only and bounded (paste_verify.VERIFY_BUDGET_S), and it never
        # sends Cmd-V again. When the paste missed, the dictation stays on the
        # clipboard for the user to paste; putting the old one back would lose
        # the only copy within reach.
        if verifier is not None and _missed(verifier, snapshot):
            return True
    except BaseException as error:
        _restore_after_failure(
            adapter, snapshot, owner_count, error,
            message="dictation pasted but clipboard wait failed", pasted=True)
    try:
        adapter.restore_if_unchanged(snapshot, owner_count)
    except Exception as error:
        raise PasteDeliveryError(
            f"dictation pasted but clipboard restore failed: {error}",
            pasted=True,
        ) from error
    return True


def paste_at_cursor(text: str, restore_clipboard: bool = True,
                    on_sent=None, verifier=None) -> bool:
    """Put text on the clipboard and paste it into the frontmost app.

    With restoration enabled (the default), a blocked paste raises a
    preservation-aware PasteDeliveryError. Set restore_clipboard=False to
    leave text available for a manual paste and receive False when blocked.
    on_sent, if given, is called once Cmd-V has been sent, before the
    clipboard-restore wait. verifier, if given, is called after that wait with
    the old clipboard's text, and returning True leaves the dictation on the
    clipboard instead of restoring. Not called when the old clipboard was
    concealed, or with restore_clipboard=False: neither restores anyway.
    """
    if not restore_clipboard:
        set_clipboard(text)
        try:
            sent = _post_cmd_v() or _applescript_cmd_v()
        except Exception:
            return False
        if sent and on_sent is not None:
            on_sent()
        return sent
    return _paste_with_adapter(
        text,
        adapter=AppKitPasteboard(),
        post_cmd_v=_post_cmd_v,
        applescript_cmd_v=_applescript_cmd_v,
        sleeper=time.sleep,
        on_sent=on_sent,
        verifier=verifier,
    )
