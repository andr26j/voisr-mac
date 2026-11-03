"""Phase 4: a stranger's install starts empty; an upgrade never touches settings."""
import json
import subprocess
from pathlib import Path

from voisr.app import DEFAULT_CONFIG, load_config
from voisr.history import HistoryStore
from voisr.ui import Api

ROOT = Path(__file__).resolve().parent.parent


def test_no_config_json_is_tracked_or_copied_by_the_installer():
    tracked = subprocess.run(["git", "ls-files", "config.json"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30).stdout
    assert tracked.strip() == "", "config.json holds the owner's dictionary"
    assert "config.json" in (ROOT / ".gitignore").read_text().split()
    installer = (ROOT / "scripts" / "install_app.sh").read_text()
    assert 'cp "$PROJECT/config.json"' not in installer


def test_the_defaults_carry_no_personal_dictionary():
    assert DEFAULT_CONFIG["dictionary"] == {}


def test_a_missing_config_loads_the_empty_defaults(tmp_path):
    cfg = load_config(tmp_path / "config.json")
    assert cfg == DEFAULT_CONFIG
    assert not (tmp_path / "config.json").exists()


def test_the_app_creates_its_own_config_on_first_save(tmp_path):
    api = Api(config_path=tmp_path / "config.json",
              history=HistoryStore(tmp_path / "h.jsonl"),
              transcribe_fn=lambda wav: "", clock=lambda: 1.0,
              clipboard_fn=lambda text: None)
    assert api.get_config()["dictionary"] == {}
    api.set_config({"dictionary": {"voiser": "Voisr"}})
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["dictionary"] == {"voiser": "Voisr"}
