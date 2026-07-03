import subprocess
import time


def set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode(), check=True)


def get_clipboard() -> str:
    return subprocess.run(["pbpaste"], capture_output=True).stdout.decode()


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
        capture_output=True, text=True)
    return proc.returncode == 0


def paste_at_cursor(text: str, restore_clipboard: bool = True) -> bool:
    """Put text on the clipboard and paste it into the frontmost app.

    Returns False if pasting was blocked (missing permissions); the text
    then stays on the clipboard for a manual paste."""
    old = get_clipboard() if restore_clipboard else None
    set_clipboard(text)
    ok = _post_cmd_v() or _applescript_cmd_v()
    if restore_clipboard and ok:
        time.sleep(0.3)  # let the paste land before swapping the clipboard back
        set_clipboard(old)
    return ok
