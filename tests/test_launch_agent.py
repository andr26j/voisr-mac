import plistlib

from voisr import launch_agent


def test_enable_writes_a_runatload_plist(tmp_path):
    plist = tmp_path / "com.local.voisr.plist"
    launch_agent.enable("/Users/x/Applications/Voisr.app", path=plist)
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "com.local.voisr"
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"] == [
        "/usr/bin/open", "/Users/x/Applications/Voisr.app"]


def test_is_enabled_reflects_the_file(tmp_path):
    plist = tmp_path / "a.plist"
    assert launch_agent.is_enabled(path=plist) is False
    launch_agent.enable("/App.app", path=plist)
    assert launch_agent.is_enabled(path=plist) is True


def test_disable_removes_the_plist(tmp_path):
    plist = tmp_path / "a.plist"
    launch_agent.enable("/App.app", path=plist)
    launch_agent.disable(path=plist)
    assert plist.exists() is False


def test_enable_and_disable_are_idempotent(tmp_path):
    plist = tmp_path / "a.plist"
    launch_agent.enable("/App.app", path=plist)
    launch_agent.enable("/App.app", path=plist)
    assert launch_agent.is_enabled(path=plist) is True
    launch_agent.disable(path=plist)
    launch_agent.disable(path=plist)
    assert launch_agent.is_enabled(path=plist) is False


def test_enable_creates_the_parent_directory(tmp_path):
    plist = tmp_path / "nested" / "dir" / "a.plist"
    launch_agent.enable("/App.app", path=plist)
    assert plist.exists() is True
