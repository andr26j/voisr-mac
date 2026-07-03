import subprocess
import time


def set_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode(), check=True)


def get_clipboard() -> str:
    return subprocess.run(["pbpaste"], capture_output=True).stdout.decode()


def paste_at_cursor(text: str, restore_clipboard: bool = True) -> bool:
    """Put text on the clipboard and send Cmd-V to the frontmost app.

    Returns False if the keystroke was blocked (no Accessibility permission);
    in that case the text stays on the clipboard for a manual paste."""
    old = get_clipboard() if restore_clipboard else None
    set_clipboard(text)
    proc = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke "v" using command down'],
        capture_output=True, text=True)
    ok = proc.returncode == 0
    if restore_clipboard and ok:
        time.sleep(0.3)  # let the paste land before swapping the clipboard back
        set_clipboard(old)
    return ok
