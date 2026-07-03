import subprocess
import time


def set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode(), check=True)


def get_clipboard() -> str:
    return subprocess.run(["pbpaste"], capture_output=True).stdout.decode()


def _post_cmd_v() -> bool:
    """Send Cmd-V as CGEvents (needs Accessibility). Preflight-gated so we
    never claim success while events would silently go nowhere."""
    try:
        import Quartz
        if not Quartz.CGPreflightPostEventAccess():
            return False
        from pynput.keyboard import Controller, Key
        kb = Controller()
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
