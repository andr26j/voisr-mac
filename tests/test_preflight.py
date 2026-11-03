"""scripts/preflight.sh: one clear message per missing prerequisite."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFLIGHT = ROOT / "scripts" / "preflight.sh"


def tool(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


def run(bin_dir: Path, snippet: str):
    # fakes first; /usr/bin:/bin for the basics, which have no brew or Homebrew python
    return subprocess.run(
        ["bash", "-c", f'source "$1" && {snippet}', "_", str(PREFLIGHT)],
        capture_output=True, text=True, timeout=60,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(bin_dir)})


def fake_python(bin_dir, version="3 14", framework=True, tmp=None):
    base = bin_dir / "fw"
    base.mkdir(exist_ok=True)
    if framework:
        (base / "Python").write_text("")
    tool(bin_dir, "python3",
         f'case "$2" in *base_prefix*) echo "{base}";; *) echo "{version}";; esac')


def test_missing_homebrew_is_named_with_a_link(tmp_path):
    proc = run(tmp_path, "check_homebrew")
    assert proc.returncode == 1
    assert "Homebrew is not installed" in proc.stderr and "https://brew.sh" in proc.stderr


def test_homebrew_present_passes(tmp_path):
    tool(tmp_path, "brew", "exit 0")
    assert run(tmp_path, "check_homebrew").returncode == 0


def test_missing_command_line_tools_says_how_to_install_them(tmp_path):
    tool(tmp_path, "xcode-select", "exit 2")
    proc = run(tmp_path, "check_compiler")
    assert proc.returncode == 1
    assert "xcode-select --install" in proc.stderr


def test_a_compiler_stub_that_cannot_compile_is_caught(tmp_path):
    # on a fresh Mac /usr/bin/cc exists but only pops the installer dialog
    tool(tmp_path, "xcode-select", "echo /Library/Developer/CommandLineTools")
    tool(tmp_path, "cc", "exit 1")
    proc = run(tmp_path, "check_compiler")
    assert proc.returncode == 1
    assert "cannot build a program" in proc.stderr


def test_a_working_compiler_passes(tmp_path):
    tool(tmp_path, "xcode-select", "echo /x")
    tool(tmp_path, "cc", 'while [ "$1" != "-o" ]; do shift; done; : > "$2"')
    assert run(tmp_path, "check_compiler").returncode == 0


def test_python_older_than_the_minimum_is_refused(tmp_path):
    fake_python(tmp_path, version="3 13")
    proc = run(tmp_path, "check_python")
    assert proc.returncode == 1
    assert "Python 3.13 is too old" in proc.stderr
    assert "brew install python@3.14" in proc.stderr


def test_the_minimum_python_and_newer_pass(tmp_path):
    for version in ("3 14", "3 15", "4 0"):
        fake_python(tmp_path, version=version)
        assert run(tmp_path, "check_python").returncode == 0, version


def test_a_non_framework_python_is_refused(tmp_path):
    fake_python(tmp_path, framework=False)
    proc = run(tmp_path, "check_python")
    assert proc.returncode == 1
    assert "not a framework build" in proc.stderr


def test_no_python_at_all_is_reported(tmp_path):
    proc = subprocess.run(
        ["/bin/bash", "-c", 'source "$1" && check_python', "_", str(PREFLIGHT)],
        capture_output=True, text=True, timeout=60,
        env={"PATH": str(tmp_path), "HOME": str(tmp_path)})
    assert proc.returncode == 1
    assert "python3 was not found" in proc.stderr


def test_every_failure_is_listed_in_one_pass(tmp_path):
    tool(tmp_path, "xcode-select", "exit 2")
    fake_python(tmp_path, version="3 9", framework=False)
    proc = run(tmp_path, "run_preflight")
    assert proc.returncode == 1
    for needle in ("Homebrew is not installed", "Command Line Tools", "too old"):
        assert needle in proc.stderr, needle


def test_the_floor_matches_what_the_code_actually_needs():
    # voisr/history.py annotates list[dict] in a class that defines list():
    # that only imports with 3.14's lazy annotations, so the floor must not
    # drop below it without that file changing
    src = PREFLIGHT.read_text()
    assert "VOISR_MIN_PY_MINOR=14" in src


def test_the_real_machine_passes_or_names_what_is_missing():
    proc = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 or "✗" in proc.stderr
