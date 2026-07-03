import subprocess

from voisr.paths import find_binary


def test_finds_binary_on_normal_path():
    assert find_binary("ls") == "/bin/ls" or find_binary("ls").endswith("/ls")


def test_finds_homebrew_binary_with_launchd_path(monkeypatch):
    # Apps launched by LaunchServices get a minimal PATH without Homebrew;
    # whisper-cli and ffmpeg must still resolve.
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    whisper = find_binary("whisper-cli")
    ffmpeg = find_binary("ffmpeg")
    assert whisper and subprocess.run([whisper, "--help"], capture_output=True).returncode == 0
    assert ffmpeg and "/" in ffmpeg


def test_missing_binary_returns_none(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert find_binary("definitely-not-a-real-binary-xyz") is None


def test_transcribe_works_with_launchd_path(monkeypatch, spoken_wav):
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    from voisr.transcriber import transcribe
    wav = spoken_wav("Path resolution works.")
    assert "path" in transcribe(wav).lower()
