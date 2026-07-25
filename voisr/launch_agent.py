"""Launch-at-login via a LaunchAgent plist.

SMAppService would be the modern API, but it requires a properly signed
bundle and install_app.sh signs ad-hoc. A LaunchAgent works unsigned.
"""
import plistlib
from pathlib import Path

LABEL = "com.local.voisr"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DEFAULT_APP = Path.home() / "Applications" / "Voisr.app"


def is_enabled(path=PLIST_PATH) -> bool:
    return Path(path).exists()


def enable(app_path=DEFAULT_APP, path=PLIST_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/open", str(app_path)],
        "RunAtLoad": True,
    }
    path.write_bytes(plistlib.dumps(payload))


def disable(path=PLIST_PATH) -> None:
    Path(path).unlink(missing_ok=True)
