"""M5 (2026-10-12 audit): after a Python minor upgrade, a reinstall reused the
old venv while the launcher was rebuilt for the new version, so the app
looked for lib/python3.15/site-packages in a 3.14 venv and couldn't start."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "scripts" / "install_lib.sh"
INSTALLER = ROOT / "scripts" / "install_app.sh"


def venv_usable(venv: Path, pyver: str) -> bool:
    proc = subprocess.run(
        ["bash", "-c", 'source "$1" && venv_usable "$2" "$3"', "_",
         str(LIB), str(venv), pyver],
        capture_output=True, text=True, timeout=30)
    # a missing lib or a typo also exits 1; only a clean answer counts
    assert proc.returncode in (0, 1) and not proc.stderr, proc.stderr
    return proc.returncode == 0


def fake_venv(tmp_path, script: str) -> Path:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python3"
    python.write_text("#!/bin/bash\n" + script + "\n")
    python.chmod(0o755)
    return venv


def test_a_venv_on_the_same_python_is_reused(tmp_path):
    venv = fake_venv(tmp_path, 'echo "3.14"')
    assert venv_usable(venv, "3.14")


def test_a_venv_from_an_older_python_is_rebuilt(tmp_path):
    venv = fake_venv(tmp_path, 'echo "3.14"')
    assert not venv_usable(venv, "3.15")


def test_a_venv_whose_python_no_longer_runs_is_rebuilt(tmp_path):
    # the interpreter a venv points at is gone after the upgrade
    venv = fake_venv(tmp_path, "exit 127")
    assert not venv_usable(venv, "3.14")


def test_a_missing_venv_is_not_usable(tmp_path):
    assert not venv_usable(tmp_path / "venv", "3.14")


def test_the_real_venv_python_reports_major_minor(tmp_path):
    # the probe the function runs must print exactly what PYVER holds
    import sys
    subprocess.run([sys.executable, "-m", "venv", "--without-pip",
                    str(tmp_path / "venv")], check=True, timeout=60)
    pyver = f"{sys.version_info[0]}.{sys.version_info[1]}"
    assert venv_usable(tmp_path / "venv", pyver)


def test_installer_checks_the_venv_version_instead_of_only_existence():
    src = INSTALLER.read_text()
    assert "install_lib.sh" in src
    assert "venv_usable" in src
    assert '[ -d "$APPSUP/venv" ] ||' not in src


# ---- L5 (2026-10-12 audit) ----

def bundle_cdhash(path) -> str:
    proc = subprocess.run(
        ["bash", "-c", 'source "$1" && bundle_cdhash "$2"', "_", str(LIB), str(path)],
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_bundle_cdhash_reads_an_ad_hoc_signature(tmp_path):
    import shutil
    binary = tmp_path / "tool"
    shutil.copy("/usr/bin/true", binary)
    subprocess.run(["codesign", "--force", "-s", "-", str(binary)],
                   check=True, capture_output=True, timeout=30)
    digest = bundle_cdhash(binary)
    assert len(digest) == 40 and int(digest, 16) >= 0


def test_bundle_cdhash_is_empty_when_there_is_nothing_to_read(tmp_path):
    assert bundle_cdhash(tmp_path / "missing.app") == ""


def test_installer_reports_grants_from_the_real_cdhash_not_a_guess():
    # it printed "grants survive" even when an icon or Info.plist change had
    # changed the cdhash, which silently revokes them
    src = INSTALLER.read_text()
    assert "grants survive" not in src
    assert src.count("bundle_cdhash") >= 2
    assert "tccutil reset" in src


def test_installer_warns_when_the_old_version_is_still_running():
    src = INSTALLER.read_text()
    assert "pgrep" in src


# ---- Dock icon cache note ----

def icon_replaced(old, new) -> bool:
    proc = subprocess.run(
        ["bash", "-c", 'set -euo pipefail; source "$1"; if icon_replaced "$2" "$3"; then echo yes; fi',
         "_", str(LIB), str(old), str(new)],
        capture_output=True, text=True, timeout=30)
    # set -e must not abort on a missing old icon
    assert proc.returncode == 0 and not proc.stderr, proc.stderr
    return proc.stdout.strip() == "yes"


def test_a_different_existing_icon_is_reported_as_replaced(tmp_path):
    (tmp_path / "old").write_bytes(b"old")
    (tmp_path / "new").write_bytes(b"new")
    assert icon_replaced(tmp_path / "old", tmp_path / "new")


def test_an_unchanged_icon_is_not_reported(tmp_path):
    (tmp_path / "old").write_bytes(b"same")
    (tmp_path / "new").write_bytes(b"same")
    assert not icon_replaced(tmp_path / "old", tmp_path / "new")


def test_a_fresh_install_is_not_reported_and_does_not_abort(tmp_path):
    (tmp_path / "new").write_bytes(b"new")
    assert not icon_replaced(tmp_path / "missing", tmp_path / "new")


def test_installer_compares_before_copying_and_prints_the_dock_commands():
    src = INSTALLER.read_text()
    assert src.index("icon_replaced") < src.index('cp "$PROJECT/assets/Voisr.icns"')
    proc = subprocess.run(["bash", "-c", 'source "$1"; dock_icon_note', "_", str(LIB)],
                          capture_output=True, text=True, timeout=30)
    assert "com.apple.dock.iconcache" in proc.stdout and "killall Dock" in proc.stdout
    # the installer only prints it; it never clears the cache itself
    assert "killall" not in src and "iconcache" not in src
