# Helpers for install_app.sh, kept separate so tests/test_install_script.py
# can source and exercise them without running an install.

# venv_usable DIR PYVER: succeeds when DIR is a venv whose python runs and is
# PYVER (major.minor). A venv built by an older Python must be rebuilt: the
# launcher is compiled against the current PYVER and puts
# lib/python$PYVER/site-packages on PYTHONPATH, which an old venv lacks.
venv_usable() {
  local venv="$1" pyver="$2" got
  [ -x "$venv/bin/python3" ] || return 1
  got="$("$venv/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || return 1
  [ "$got" = "$pyver" ]
}

# bundle_cdhash PATH: the code hash macOS pins an ad-hoc-signed app's
# Input Monitoring / Accessibility grants to; empty when unsigned or absent.
bundle_cdhash() {
  codesign -dvvv "$1" 2>&1 | sed -n 's/^CDHash=//p' || true
}
