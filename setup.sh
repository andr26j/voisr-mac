#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
command -v whisper-cli >/dev/null || brew install whisper-cpp
# ffmpeg is the fallback recorder (when the built-in audio stream can't open)
# and converts non-WAV files for `--mode file`. Dictating in the app doesn't
# need it while the stream works, but without it the fallback fails.
command -v ffmpeg >/dev/null || brew install ffmpeg
mkdir -p models
# -f: an HTTP error must fail loudly, not write an error page into the .bin.
# Download to .part and rename: an interrupted download left a truncated
# .bin that the [ -f ] check then skipped on every later run.
fetch_model() {
  local name="$1"
  [ -f "models/$name" ] && return
  curl -fL --retry 3 -o "models/$name.part" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$name"
  mv "models/$name.part" "models/$name"
}
fetch_model ggml-base.en.bin
fetch_model ggml-small.en.bin
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt
echo "setup complete"
