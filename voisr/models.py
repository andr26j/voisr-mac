"""Speech model registry, install state and downloads.

Every model Voisr offers is described here: the file it lives in, its exact
size and SHA-256 (from the whisper.cpp model repo's published metadata), and
what it needs from the Mac. Models are never bundled; each person downloads
only what they choose, into ``models/``.

``BENCHMARKS`` is empty on purpose. No corpus has been recorded yet, so no
speed or accuracy figure exists. When one does, add an entry keyed by model id
and the Settings list will show it; nothing else changes.
"""
from __future__ import annotations

import functools
import hashlib
import platform
import ssl
import shutil
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DOWNLOAD_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

# Free space left over after a download, so the Mac isn't filled to the brim.
DISK_HEADROOM_BYTES = 512 * 1024 * 1024
GIB = 1024 ** 3


@dataclass(frozen=True)
class Model:
    id: str
    engine: str
    label: str
    description: str
    filename: str
    bytes: int
    sha256: str
    min_memory_gb: int       # conservative, NOT measured: see BENCHMARKS
    apple_silicon_only: bool
    license: str


# Sizes and checksums are the LFS metadata of huggingface.co/ggerganov/
# whisper.cpp (tree API, 2026-11-03). Weights are OpenAI Whisper's, MIT.
REGISTRY: tuple[Model, ...] = (
    Model("base.en", "whisper.cpp", "Base (English)",
          "The smallest of the four. English only.",
          "ggml-base.en.bin", 147_964_211,
          "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002",
          4, False, "MIT"),
    Model("small.en", "whisper.cpp", "Small (English)",
          "A step up in size from Base. English only. The default.",
          "ggml-small.en.bin", 487_614_201,
          "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d",
          4, False, "MIT"),
    Model("medium.en-q5_0", "whisper.cpp", "Medium (English, compressed)",
          "A larger model, compressed (5-bit) to save disk. English only.",
          "ggml-medium.en-q5_0.bin", 539_225_533,
          "76733e26ad8fe1c7a5bf7531a9d41917b2adc0f20f2e4f5531688a8c6cd88eb0",
          12, False, "MIT"),
    Model("large-v3-turbo-q5_0", "whisper.cpp", "Large v3 Turbo (compressed)",
          "The largest model offered, compressed (5-bit). Multilingual model, "
          "always run in English.",
          "ggml-large-v3-turbo-q5_0.bin", 574_041_195,
          "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
          12, False, "MIT"),
)

# Measured figures, keyed by model id, e.g.
#   "small.en": {"wer": 0.08, "median_s": 0.9, "peak_mb": 900}
# Empty until the benchmark corpus is recorded (scripts/bench_models.py).
BENCHMARKS: dict[str, dict] = {}

_BY_ID = {m.id: m for m in REGISTRY}
_FILENAMES = {m.filename for m in REGISTRY}


def get_model(model_id) -> Model | None:
    return _BY_ID.get(model_id)


def filename_for(model_id: str) -> str:
    """Registered file name, else the ggml-<name>.bin convention that custom
    and pre-registry configs use."""
    m = _BY_ID.get(model_id)
    return m.filename if m else f"ggml-{model_id}.bin"


def id_from_path(path) -> str:
    name = Path(path).name
    for m in REGISTRY:
        if m.filename == name:
            return m.id
    return Path(path).stem.removeprefix("ggml-")


def discover_custom(models_dir: Path = MODELS_DIR) -> list[Path]:
    """Other ggml-*.bin files dropped into models/. .part files never match
    the glob; registry files are listed as themselves, not as Custom."""
    d = Path(models_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("ggml-*.bin")
                  if p.is_file() and p.name not in _FILENAMES)


def installed_paths(models_dir: Path = MODELS_DIR) -> dict[str, Path]:
    """id -> path for every usable model on disk, registry and custom."""
    d = Path(models_dir)
    found = {m.id: d / m.filename for m in REGISTRY if (d / m.filename).is_file()}
    for p in discover_custom(d):
        found[id_from_path(p)] = p
    return found


def ssl_context() -> ssl.SSLContext:
    """python.org's Python ships no CA bundle, so urllib fails TLS against
    Hugging Face; use certifi's, which requirements.txt pins."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


@functools.lru_cache(maxsize=1)
def system_info() -> dict:
    """Chip and memory of this Mac, read at runtime."""
    memory_gb = None
    chip = None
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        memory_gb = round(int(out) / GIB, 1)
    except Exception:
        pass
    try:
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                              capture_output=True, text=True, timeout=5
                              ).stdout.strip() or None
    except Exception:
        pass
    return {"memory_gb": memory_gb, "chip": chip,
            "apple_silicon": platform.machine() == "arm64"}


def restriction(model: Model, system: dict) -> tuple[str | None, str | None]:
    """(blocked reason, warning) for this model on this Mac. A model that
    can't run is blocked; one that merely needs more memory than the Mac has
    only warns, since the requirement is a conservative estimate."""
    if model.apple_silicon_only and not system.get("apple_silicon", True):
        return "Needs an Apple Silicon Mac.", None
    mem = system.get("memory_gb")
    if mem is not None and model.min_memory_gb > mem:
        return None, (f"Wants about {model.min_memory_gb} GB of memory; "
                      f"this Mac has {mem:g} GB. It may run slowly.")
    return None, None


def list_models(models_dir: Path = MODELS_DIR, system: dict | None = None,
                active: str | None = None) -> list[dict]:
    system = system if system is not None else system_info()
    d = Path(models_dir)
    installed = installed_paths(d)
    rows = []
    for m in REGISTRY:
        blocked, warning = restriction(m, system)
        p = installed.get(m.id)
        rows.append({
            "id": m.id, "label": m.label, "description": m.description,
            "bytes": m.bytes, "installed": p is not None,
            "active": m.id == active, "custom": False,
            "min_memory_gb": m.min_memory_gb,
            "apple_silicon_only": m.apple_silicon_only,
            "license": m.license, "blocked": blocked, "warning": warning,
            "benchmark": BENCHMARKS.get(m.id),
        })
    for mid, p in installed.items():
        if mid in _BY_ID:
            continue
        rows.append({
            "id": mid, "label": mid, "description": "Your own model file.",
            "bytes": p.stat().st_size, "installed": True,
            "active": mid == active, "custom": True, "min_memory_gb": None,
            "apple_silicon_only": False, "license": None, "blocked": None,
            "warning": None, "benchmark": None,
        })
    return rows


def check_disk_space(model: Model, models_dir: Path = MODELS_DIR,
                     free_bytes: int | None = None) -> str | None:
    """None if the model fits, else a message saying how much is missing."""
    if free_bytes is None:
        d = Path(models_dir)
        d.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(d).free
    need = model.bytes + DISK_HEADROOM_BYTES
    if free_bytes >= need:
        return None
    mb = lambda n: f"{n / 1024 ** 2:.0f} MB"
    return (f"Not enough disk space for {model.label}: it needs {mb(model.bytes)} "
            f"plus {mb(DISK_HEADROOM_BYTES)} to spare, and only "
            f"{mb(max(free_bytes, 0))} is free.")


class DownloadCancelled(Exception):
    pass


class ModelDownloader:
    """One download at a time, on its own thread. Bytes go to <file>.part,
    are hashed as they arrive, and are renamed to the .bin only when size and
    SHA-256 match, so no code path leaves a broken .bin behind."""

    CHUNK = 1024 * 256

    def __init__(self, models_dir: Path = MODELS_DIR, base_url: str = DOWNLOAD_BASE,
                 free_bytes_fn=None, registry=REGISTRY):
        self._dir = Path(models_dir)
        self._base = base_url.rstrip("/")
        self._free_bytes_fn = free_bytes_fn
        self._registry = {m.id: m for m in registry}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state: dict = {}
        self._seq = 0  # one per download, so the page reports each outcome once

    def start(self, model_id: str, system: dict | None = None) -> dict:
        m = self._registry.get(model_id)
        if m is None:
            return {"error": f"Unknown model: {model_id}"}
        blocked, _ = restriction(m, system if system is not None else system_info())
        if blocked:
            return {"error": f"{m.label}: {blocked}"}
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"error": "Another download is already running."}
            if (self._dir / m.filename).is_file():
                return {"error": f"{m.label} is already installed."}
            free = self._free_bytes_fn() if self._free_bytes_fn else None
            msg = check_disk_space(m, self._dir, free)
            if msg:
                return {"error": msg}
            self._cancel.clear()
            self._seq += 1
            self._state = {"id": m.id, "state": "downloading", "done": 0,
                           "total": m.bytes, "error": None, "seq": self._seq}
            self._thread = threading.Thread(target=self._run, args=(m,),
                                            name="model-download", daemon=True)
            self._thread.start()
        return {"ok": True}

    def progress(self) -> dict:
        with self._lock:
            return dict(self._state) if self._state else {"state": "idle"}

    def active_id(self) -> str | None:
        with self._lock:
            if self._state.get("state") == "downloading":
                return self._state["id"]
        return None

    def cancel(self, wait: float = 5.0):
        """Stop any download and remove its .part. Called on Quit."""
        self._cancel.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(wait)

    def join(self, timeout: float | None = None):
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _run(self, m: Model):
        part = self._dir / (m.filename + ".part")
        final = self._dir / m.filename
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            done = 0
            req = urllib.request.Request(f"{self._base}/{m.filename}",
                                         headers={"User-Agent": "Voisr"})
            with urllib.request.urlopen(req, timeout=30,
                                        context=ssl_context()) as resp, \
                    open(part, "wb") as out:
                while True:
                    if self._cancel.is_set():
                        raise DownloadCancelled()
                    chunk = resp.read(self.CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    self._set(done=done)
                    if done > m.bytes:
                        raise ValueError("The download is larger than expected.")
            if done != m.bytes:
                raise ValueError(
                    f"Incomplete download: got {done} of {m.bytes} bytes.")
            if digest.hexdigest() != m.sha256:
                raise ValueError("Checksum mismatch: the file was corrupted "
                                 "or has changed. It was discarded.")
            part.replace(final)
            self._set(state="done", done=m.bytes)
        except DownloadCancelled:
            part.unlink(missing_ok=True)
            self._set(state="cancelled")
        except Exception as e:
            part.unlink(missing_ok=True)
            self._set(state="error", error=str(e) or type(e).__name__)
