#!/bin/bash
# One command to install Voisr: checks this Mac, fetches the speech engine and
# models (setup.sh), then builds the app (scripts/install_app.sh).
# Safe to re-run to update: it never overwrites your settings or history.
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=scripts/preflight.sh
source scripts/preflight.sh
echo "==> Checking this Mac"
if ! run_preflight; then
  echo >&2
  echo "Fix the above, then run ./install.sh again. Nothing was installed." >&2
  exit 1
fi

echo "==> Fetching the speech engine and models (setup.sh)"
bash setup.sh

echo "==> Building the app (scripts/install_app.sh)"
bash scripts/install_app.sh

cat <<'DONE'

Voisr is installed. Open it with:  open ~/Applications/Voisr.app
Then follow "Permissions" in the README to allow Microphone and Accessibility.
DONE
