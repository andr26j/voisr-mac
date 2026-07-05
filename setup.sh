#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
command -v whisper-cli >/dev/null || brew install whisper-cpp
mkdir -p models
# -f: an HTTP error must fail loudly, not write an error page into the .bin
[ -f models/ggml-base.en.bin ] || curl -fL --retry 3 -o models/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
[ -f models/ggml-small.en.bin ] || curl -fL --retry 3 -o models/ggml-small.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt
echo "setup complete"
