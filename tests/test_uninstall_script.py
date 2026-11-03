"""scripts/uninstall.sh against a throwaway HOME, never the real one."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNINSTALL = ROOT / "scripts" / "uninstall.sh"


def make_install(home: Path) -> dict:
    app = home / "Applications" / "Voisr.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "Voisr").write_text("bin")
    appsup = home / "Library" / "Application Support" / "Voisr"
    for sub in ("voisr", "venv/bin", "models", "data"):
        (appsup / sub).mkdir(parents=True)
    (appsup / "models" / "ggml-base.en.bin").write_text("m")
    (appsup / "requirements.txt").write_text("x")
    (appsup / ".launcher-build-sig").write_text("sig")
    (appsup / "config.json").write_text('{"dictionary": {"a": "B"}}')
    (appsup / "data" / "history.jsonl").write_text('{"text": "hello"}\n')
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.local.voisr.plist").write_text("plist")
    logs = home / "Library" / "Logs"
    logs.mkdir(parents=True)
    (logs / "Voisr.log").write_text("log")
    (home / "Library" / "Caches" / "com.local.voisr").mkdir(parents=True)
    (home / "Library" / "WebKit" / "com.local.voisr").mkdir(parents=True)
    (home / "Library" / "Caches" / "SomeoneElse").mkdir()
    return {"app": home / "Applications" / "Voisr.app", "appsup": appsup,
            "plist": agents / "com.local.voisr.plist", "log": logs / "Voisr.log"}


def run(home: Path, *args, answers=""):
    return subprocess.run(
        ["bash", str(UNINSTALL), *args], input=answers, capture_output=True,
        text=True, timeout=60,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
             "VOISR_UNINSTALL_STDIN": "1"})


def test_no_to_both_questions_keeps_history_and_dictionary(tmp_path):
    p = make_install(tmp_path)
    proc = run(tmp_path, answers="n\nn\n")
    assert proc.returncode == 0, proc.stderr
    assert not p["app"].exists() and not p["plist"].exists() and not p["log"].exists()
    assert not (p["appsup"] / "venv").exists() and not (p["appsup"] / "models").exists()
    assert not (p["appsup"] / ".launcher-build-sig").exists()
    assert (p["appsup"] / "data" / "history.jsonl").read_text() == '{"text": "hello"}\n'
    assert (p["appsup"] / "config.json").exists()


def test_each_question_is_independent(tmp_path):
    p = make_install(tmp_path)
    run(tmp_path, answers="y\nn\n")   # history yes, settings no
    assert not (p["appsup"] / "data").exists()
    assert (p["appsup"] / "config.json").exists()


def test_yes_to_both_removes_the_whole_runtime(tmp_path):
    p = make_install(tmp_path)
    run(tmp_path, answers="y\ny\n")
    assert not p["appsup"].exists()
    assert not (tmp_path / "Library/Caches/com.local.voisr").exists()
    assert not (tmp_path / "Library/WebKit/com.local.voisr").exists()


def test_other_apps_files_are_left_alone(tmp_path):
    make_install(tmp_path)
    run(tmp_path, "--delete-data")
    assert (tmp_path / "Library/Caches/SomeoneElse").exists()


def test_with_no_answer_available_the_data_is_kept(tmp_path):
    p = make_install(tmp_path)
    proc = subprocess.run(
        ["bash", str(UNINSTALL)], stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=60,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    assert proc.returncode == 0
    assert (p["appsup"] / "config.json").exists()
    assert (p["appsup"] / "data" / "history.jsonl").exists()


def test_dry_run_changes_nothing(tmp_path):
    p = make_install(tmp_path)
    proc = run(tmp_path, "--dry-run", "--delete-data")
    assert proc.returncode == 0
    assert p["app"].exists() and p["plist"].exists() and p["log"].exists()
    assert (p["appsup"] / "venv").exists() and (p["appsup"] / "config.json").exists()
    assert "would run" in proc.stdout


def test_a_bare_machine_is_not_an_error(tmp_path):
    assert run(tmp_path).returncode == 0


def test_an_unknown_option_is_rejected(tmp_path):
    assert run(tmp_path, "--wipe").returncode == 2
