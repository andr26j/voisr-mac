"""Runs the Node behavioural suite for the web layer.

The web layer's pytest tests are structural (they grep source text); this
bridge runs real behaviour through node:test. Zero npm dependencies — node's
built-in runner only. Skips cleanly where node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent

# Node >= 24 treats a positional path as a glob pattern, not a directory --
# `node --test tests/js` tries to load the directory as a module and fails.
# Node expands this itself; no shell is involved.
TESTS_GLOB = "tests/js/*.test.mjs"


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_js_behaviour_suite():
    proc = subprocess.run(
        [NODE, "--test", TESTS_GLOB],
        capture_output=True, text=True, timeout=120, cwd=ROOT,
    )
    assert proc.returncode == 0, (
        f"node --test failed\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
