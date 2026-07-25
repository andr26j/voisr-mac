"""Runs the Node behavioural suite for the web layer.

The web layer's pytest tests are structural (they grep source text); this
bridge runs real behaviour through node:test. Zero npm dependencies — node's
built-in runner only. Skips cleanly where node is unavailable.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent

# node's runner summarises with a line like "ℹ tests 34". A glob that matches
# nothing still exits 0 with "ℹ tests 0", so returncode alone would let a
# renamed file or moved directory turn this bridge silently green.
TESTS_RUN_RE = re.compile(r"^\D*tests (\d+)$", re.MULTILINE)

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

    counts = TESTS_RUN_RE.findall(proc.stdout)
    assert counts, (
        f"could not find node's test count in its output; did the runner's "
        f"summary format change?\n--- stdout ---\n{proc.stdout}"
    )
    assert int(counts[-1]) > 0, (
        f"no JS tests ran -- {TESTS_GLOB!r} matched nothing (a renamed file or "
        f"a moved directory will do this, and node still exits 0)."
        f"\n--- stdout ---\n{proc.stdout}"
    )
