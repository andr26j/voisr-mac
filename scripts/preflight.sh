# Checks that a Mac can build and run Voisr, one clear message per failure.
# Sourced by install.sh and tests/test_preflight.py; run directly it checks
# and exits non-zero if anything is missing:  bash scripts/preflight.sh

# The oldest Python the code runs on. Found by importing the package on
# 3.10 (the version that fails first): voisr/cleanup.py uses regex atomic
# groups (3.11+), and voisr/history.py annotates with list[dict] inside a
# class that defines list(), which only works with 3.14's lazy annotations.
# Nothing between 3.11 and 3.13 was run, so 3.14 is the tested floor.
VOISR_MIN_PY_MAJOR=3
VOISR_MIN_PY_MINOR=14

_pf_fail() {
  echo "✗ $1" >&2
  shift
  local line
  for line in "$@"; do echo "    $line" >&2; done
  PF_FAILED=1
}

check_macos() {
  [ "$(uname -s)" = "Darwin" ] && return 0
  _pf_fail "Voisr only runs on macOS." "This is $(uname -s)."
  return 1
}

check_homebrew() {
  command -v brew >/dev/null 2>&1 && return 0
  _pf_fail "Homebrew is not installed (or not on your PATH)." \
    "It installs whisper.cpp and ffmpeg. Get it from https://brew.sh," \
    "then open a new Terminal window and run this again."
  return 1
}

check_compiler() {
  local dir out
  if ! xcode-select -p >/dev/null 2>&1; then
    _pf_fail "The Xcode Command Line Tools are not installed." \
      "Voisr builds a small launcher with the C compiler (cc). Run:" \
      "  xcode-select --install" \
      "and run this again when it finishes."
    return 1
  fi
  dir="$(mktemp -d)" || return 1
  printf 'int main(void){return 0;}\n' > "$dir/t.c"
  if ! cc -o "$dir/t" "$dir/t.c" >/dev/null 2>&1; then
    rm -rf "$dir"
    _pf_fail "The C compiler (cc) is present but cannot build a program." \
      "Reinstall the Command Line Tools:" \
      "  sudo rm -rf /Library/Developer/CommandLineTools && xcode-select --install"
    return 1
  fi
  rm -rf "$dir"
}

# python3 on PATH is what setup.sh and install_app.sh both use.
check_python() {
  local py ver major minor base
  py="$(command -v python3 2>/dev/null)" || {
    _pf_fail "python3 was not found." \
      "Install it with: brew install python@$VOISR_MIN_PY_MAJOR.$VOISR_MIN_PY_MINOR"
    return 1
  }
  ver="$("$py" -c 'import sys; print("%d %d" % sys.version_info[:2])' 2>/dev/null)" || {
    _pf_fail "$py does not run." "Reinstall Python: brew install python@$VOISR_MIN_PY_MAJOR.$VOISR_MIN_PY_MINOR"
    return 1
  }
  major="${ver% *}"; minor="${ver#* }"
  if [ "$major" -lt "$VOISR_MIN_PY_MAJOR" ] ||
     { [ "$major" -eq "$VOISR_MIN_PY_MAJOR" ] && [ "$minor" -lt "$VOISR_MIN_PY_MINOR" ]; }; then
    _pf_fail "Python $major.$minor is too old (python3 is $py)." \
      "Voisr needs Python $VOISR_MIN_PY_MAJOR.$VOISR_MIN_PY_MINOR or newer. Run:" \
      "  brew install python@$VOISR_MIN_PY_MAJOR.$VOISR_MIN_PY_MINOR" \
      "then open a new Terminal window so 'python3' points at it."
    return 1
  fi
  # The launcher loads Python as a library, so it must be a framework build:
  # Homebrew and python.org Python are; the Xcode and pyenv/uv ones may not be.
  base="$("$py" -c 'import sys; print(sys.base_prefix)')"
  if [ ! -f "$base/Python" ]; then
    _pf_fail "python3 ($py) is not a framework build of Python." \
      "Voisr's launcher needs $base/Python, which does not exist." \
      "Use Homebrew's or python.org's Python: brew install python@$VOISR_MIN_PY_MAJOR.$VOISR_MIN_PY_MINOR" \
      "then open a new Terminal window."
    return 1
  fi
}

# Runs every check so one pass lists everything that needs fixing.
run_preflight() {
  PF_FAILED=0
  check_macos || true
  check_homebrew || true
  check_compiler || true
  check_python || true
  [ "$PF_FAILED" -eq 0 ]
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  if run_preflight; then echo "✓ This Mac is ready for Voisr."; else exit 1; fi
fi
