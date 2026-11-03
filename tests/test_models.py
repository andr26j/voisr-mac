import hashlib
import http.server
import threading
import time
from pathlib import Path

import pytest

from voisr import models
from voisr.models import Model, ModelDownloader, REGISTRY
from voisr.transcriber import resolve_model

PAYLOAD = b"x" * 300_000


def _model(payload=PAYLOAD, sha=None):
    return Model("fake.en", "whisper.cpp", "Fake", "d", "ggml-fake.en.bin",
                 len(payload), sha or hashlib.sha256(payload).hexdigest(),
                 4, False, "MIT")


@pytest.fixture
def server():
    """Local HTTP server. Set .mode: ok | short | slow | big."""
    state = {"mode": "ok", "payload": PAYLOAD}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = state["payload"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            mode = state["mode"]
            if mode == "short":
                self.wfile.write(body[: len(body) // 2])
                self.wfile.flush()
                self.close_connection = True
                return  # connection closes with bytes missing
            if mode == "slow":
                for i in range(0, len(body), 10_000):
                    try:
                        self.wfile.write(body[i:i + 10_000])
                        self.wfile.flush()
                    except OSError:
                        return
                    time.sleep(0.05)
                return
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()


def _downloader(tmp_path, server, model, **kw):
    return ModelDownloader(tmp_path, server["url"], registry=(model,), **kw)


# ---- registry ----

def test_registry_entries_are_well_formed():
    ids = [m.id for m in REGISTRY]
    assert len(set(ids)) == len(ids)
    for m in REGISTRY:
        assert m.filename == f"ggml-{m.id}.bin"
        assert len(m.sha256) == 64 and int(m.sha256, 16) >= 0
        assert m.bytes > 0 and m.min_memory_gb > 0
    assert {"base.en", "small.en"} <= set(ids)


def test_registry_is_frozen():
    with pytest.raises(Exception):
        REGISTRY[0].bytes = 1


def test_benchmarks_are_empty_until_measured():
    assert models.BENCHMARKS == {}
    assert all(r["benchmark"] is None
               for r in models.list_models(Path("/nonexistent"), {}))


# ---- resolve_model / discovery ----

def test_resolve_model_prefers_configured_then_small_then_base(tmp_path):
    (tmp_path / "ggml-base.en.bin").write_bytes(b"x")
    assert resolve_model("small.en", tmp_path).name == "ggml-base.en.bin"
    (tmp_path / "ggml-small.en.bin").write_bytes(b"x")
    assert resolve_model(None, tmp_path).name == "ggml-small.en.bin"
    assert resolve_model("base.en", tmp_path).name == "ggml-base.en.bin"
    assert resolve_model("no-such-model", tmp_path).name == "ggml-small.en.bin"


def test_resolve_model_knows_registry_and_custom_names(tmp_path):
    (tmp_path / "ggml-medium.en-q5_0.bin").write_bytes(b"x")
    (tmp_path / "ggml-mine.bin").write_bytes(b"x")
    assert resolve_model("medium.en-q5_0", tmp_path).name == "ggml-medium.en-q5_0.bin"
    assert resolve_model("mine", tmp_path).name == "ggml-mine.bin"


def test_resolve_model_with_nothing_installed_names_base(tmp_path):
    assert resolve_model("whatever", tmp_path).name == "ggml-base.en.bin"


def test_custom_discovery_skips_part_files_and_registry_files(tmp_path):
    (tmp_path / "ggml-mine.bin").write_bytes(b"12345")
    (tmp_path / "ggml-big.bin.part").write_bytes(b"x")
    (tmp_path / "ggml-small.en.bin").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    assert [p.name for p in models.discover_custom(tmp_path)] == ["ggml-mine.bin"]
    rows = {r["id"]: r for r in models.list_models(tmp_path, {})}
    assert rows["mine"]["custom"] and rows["mine"]["installed"]
    assert rows["mine"]["bytes"] == 5
    assert rows["small.en"]["installed"] and not rows["base.en"]["installed"]
    assert "big" not in rows


# ---- Mac checks ----

def test_memory_warning_and_intel_block():
    m = Model("a", "e", "A", "d", "ggml-a.bin", 1, "0" * 64, 12, False, "MIT")
    assert models.restriction(m, {"memory_gb": 8, "apple_silicon": True})[1]
    assert models.restriction(m, {"memory_gb": 16, "apple_silicon": True}) == (None, None)
    assert models.restriction(m, {"memory_gb": None}) == (None, None)
    a = Model("b", "e", "B", "d", "ggml-b.bin", 1, "0" * 64, 4, True, "MIT")
    assert models.restriction(a, {"memory_gb": 16, "apple_silicon": False})[0]
    assert models.restriction(a, {"memory_gb": 16, "apple_silicon": True}) == (None, None)


# ---- disk space ----

def test_disk_check_refuses_with_a_clear_message():
    m = _model()
    msg = models.check_disk_space(m, free_bytes=1000)
    assert "Not enough disk space" in msg and "Fake" in msg
    assert models.check_disk_space(m, free_bytes=10 ** 10) is None


def test_download_refused_when_it_will_not_fit(tmp_path, server):
    d = _downloader(tmp_path, server, _model(), free_bytes_fn=lambda: 1000)
    res = d.start("fake.en", {})
    assert "Not enough disk space" in res["error"]
    assert list(tmp_path.iterdir()) == []


# ---- downloads ----

def test_download_verifies_and_renames(tmp_path, server):
    d = _downloader(tmp_path, server, _model(), free_bytes_fn=lambda: 10 ** 10)
    assert d.start("fake.en", {}) == {"ok": True}
    d.join(10)
    assert d.progress()["state"] == "done"
    assert (tmp_path / "ggml-fake.en.bin").read_bytes() == PAYLOAD
    assert not (tmp_path / "ggml-fake.en.bin.part").exists()


def test_checksum_failure_removes_the_part_and_leaves_no_bin(tmp_path, server):
    d = _downloader(tmp_path, server, _model(sha="0" * 64),
                    free_bytes_fn=lambda: 10 ** 10)
    d.start("fake.en", {})
    d.join(10)
    p = d.progress()
    assert p["state"] == "error" and "Checksum" in p["error"]
    assert list(tmp_path.iterdir()) == []


def test_interrupted_download_leaves_no_bin(tmp_path, server):
    server["mode"] = "short"
    d = _downloader(tmp_path, server, _model(), free_bytes_fn=lambda: 10 ** 10)
    d.start("fake.en", {})
    d.join(10)
    assert d.progress()["state"] == "error"
    assert list(tmp_path.iterdir()) == []


def test_wrong_size_is_rejected(tmp_path, server):
    server["payload"] = PAYLOAD + b"extra"
    d = _downloader(tmp_path, server, _model(), free_bytes_fn=lambda: 10 ** 10)
    d.start("fake.en", {})
    d.join(10)
    assert d.progress()["state"] == "error"
    assert list(tmp_path.iterdir()) == []


def test_cancel_mid_download_leaves_nothing(tmp_path, server):
    server["mode"] = "slow"
    d = _downloader(tmp_path, server, _model(), free_bytes_fn=lambda: 10 ** 10)
    d.start("fake.en", {})
    deadline = time.time() + 5
    while d.progress().get("done", 0) == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert d.active_id() == "fake.en"
    d.cancel()
    assert d.progress()["state"] == "cancelled"
    assert list(tmp_path.iterdir()) == []


def test_only_one_download_at_a_time(tmp_path, server):
    server["mode"] = "slow"
    d = _downloader(tmp_path, server, _model(), free_bytes_fn=lambda: 10 ** 10)
    assert d.start("fake.en", {}) == {"ok": True}
    assert "already running" in d.start("fake.en", {})["error"]
    d.cancel()


def test_unknown_blocked_and_installed_models_are_refused(tmp_path, server):
    d = _downloader(tmp_path, server, _model(), free_bytes_fn=lambda: 10 ** 10)
    assert "Unknown" in d.start("nope", {})["error"]
    (tmp_path / "ggml-fake.en.bin").write_bytes(b"x")
    assert "already installed" in d.start("fake.en", {})["error"]
    intel = Model("i", "e", "I", "d", "ggml-i.bin", 5, "0" * 64, 4, True, "MIT")
    d2 = ModelDownloader(tmp_path, server["url"], registry=(intel,))
    assert "Apple Silicon" in d2.start("i", {"apple_silicon": False})["error"]


def test_ssl_context_uses_certifi_bundle():
    import ssl
    certifi = pytest.importorskip("certifi")
    ctx = models.ssl_context()
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED
    assert "certifi" in certifi.where()


def test_requirements_pin_certifi():
    reqs = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    assert any(l.startswith("certifi==") for l in reqs.splitlines())


def test_system_info_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(models.subprocess, "run",
                        lambda *a, **k: calls.append(a) or type("R", (), {"stdout": "8589934592"})())
    models.system_info.cache_clear()
    models.system_info(); models.system_info()
    assert len(calls) == 2  # memsize + brand string, once
    models.system_info.cache_clear()
