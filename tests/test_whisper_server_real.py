"""The real whisper-server binary and a real model: the prompt reaches the
server and changes the text, nothing listens beyond loopback, and the server
dies with its parent even when the parent is SIGKILLed. Skipped where the
binary or the base.en model isn't installed. The model is only read."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from voisr.paths import find_binary
from voisr.whisper_server import WhisperServer

MODEL = Path.home() / "Library/Application Support/Voisr/models/ggml-base.en.bin"

pytestmark = [
    pytest.mark.audio,
    pytest.mark.skipif(not find_binary("whisper-server") or not MODEL.is_file(),
                       reason="needs whisper-server and the base.en model"),
]

SENTENCE = "We hired Zorblax Quindleton to build the Kestrelway dashboard."
PROMPT = "Glossary: Zorblaks Kwindelton, Kestrelwey."


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_for(predicate, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def server_pid(state):
    from voisr.whisper_server import state_files
    return json.loads(state_files(state)[0].read_text())["server"]


def test_prompt_reaches_the_server_and_biases_spelling(tmp_path, spoken_wav):
    wav = spoken_wav(SENTENCE)
    server = WhisperServer(tmp_path / "state.json")
    try:
        plain = [server.transcribe(wav, MODEL)[0] for _ in range(3)]
        primed = [server.transcribe(wav, MODEL, prompt=PROMPT)[0] for _ in range(3)]
    finally:
        server.shutdown()
    print("without prompt:", plain)
    print("with prompt:   ", primed)
    # the spellings exist only in the prompt; the audio alone never yields them
    assert not any("Kwindelton" in t or "Zorblaks" in t for t in plain)
    assert all("Kwindelton" in t and "Zorblaks" in t for t in primed)


def test_server_listens_on_loopback_only_and_quit_frees_it(tmp_path, spoken_wav):
    wav = spoken_wav("Hello there.")
    state = tmp_path / "state.json"
    server = WhisperServer(state)
    server.transcribe(wav, MODEL)
    pid = server_pid(state)
    out = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
                         capture_output=True, text=True).stdout
    assert "127.0.0.1:" in out and "*:" not in out and "0.0.0.0" not in out, out
    server.shutdown()
    assert wait_for(lambda: not alive(pid), 6)
    from voisr.whisper_server import state_files
    assert not state_files(state)


CHILD = """
import sys, time
from pathlib import Path
from voisr.whisper_server import WhisperServer
s = WhisperServer(Path(sys.argv[2]))
s.prewarm(Path(sys.argv[1]))
while not (s._proc and s._ready):
    time.sleep(0.05)
print("loaded", flush=True)
time.sleep(600)
"""


def test_sigkilled_parent_leaves_no_whisper_server(tmp_path):
    state = tmp_path / "state.json"
    parent = subprocess.Popen([sys.executable, "-c", CHILD, str(MODEL), str(state)],
                              stdout=subprocess.PIPE, text=True, cwd=os.getcwd())
    pid = guard = None
    try:
        assert parent.stdout.readline().strip() == "loaded"
        from voisr.whisper_server import state_files
        info = json.loads(state_files(state)[0].read_text())
        pid, guard = info["server"], info["guard"]
        assert alive(pid) and alive(guard)
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait()
        assert wait_for(lambda: not alive(pid), 8), "whisper-server outlived its parent"
        assert wait_for(lambda: not alive(guard), 8)
    finally:
        for p in (pid, guard, parent.pid):
            if p:
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass


def test_on_screen_terms_bias_both_transcribers(tmp_path, spoken_wav):
    """A word read off the screen, through the same path a dictation takes,
    changes the spelling in whisper-server and in whisper-cli."""
    from voisr.ax_context import FocusContext
    from voisr.context_terms import context_terms
    from voisr.transcriber import transcribe_routed
    from voisr.ui import glossary_prompt

    wav = spoken_wav(SENTENCE)
    focus = FocusContext(before_text="ask ", after_text="Zorblaks Kwindelton about Kestrelwey")
    prompt = glossary_prompt({}, context_terms(focus))
    assert prompt == "Glossary: Zorblaks, Kwindelton, Kestrelwey."
    server = WhisperServer(tmp_path / "state.json")
    try:
        warm, warm_info = transcribe_routed(wav, MODEL, prompt=prompt, server=server)
        warm_plain, _ = transcribe_routed(wav, MODEL, server=server)
    finally:
        server.shutdown()
    cli, cli_info = transcribe_routed(wav, MODEL, prompt=prompt)
    cli_plain, _ = transcribe_routed(wav, MODEL)
    assert warm_info["mode"] == "warm" and cli_info["mode"] == "cli"
    print("server:", warm_plain, "->", warm)
    print("cli:", cli_plain, "->", cli)
    assert "Kestrelwey" in warm or "Zorblaks" in warm
    assert "Kestrelwey" in cli or "Zorblaks" in cli
