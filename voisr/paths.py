import os
import shutil

# Apps launched by LaunchServices/Finder get a minimal PATH that lacks the
# package-manager prefixes where whisper-cli and ffmpeg live.
EXTRA_BIN_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]


def find_binary(name: str) -> str | None:
    """Resolve a CLI tool by PATH first, then well-known install prefixes."""
    found = shutil.which(name)
    if found:
        return found
    for d in EXTRA_BIN_DIRS:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
