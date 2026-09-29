#!/usr/bin/env bash
# Build the wheel, install it into a fresh virtualenv, and run the end-to-end
# suite against the *installed* package — from outside the source tree, so the
# local `headless/` directory cannot shadow it. This is what users get from PyPI.
#
#   scripts/e2e.sh                 # impersonate + http extras
#   EXTRAS=all scripts/e2e.sh      # also Playwright (downloads Chromium)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
EXTRAS="${EXTRAS:-impersonate,http}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> building wheel"
"$PYTHON" -m pip install -q build
"$PYTHON" -m build --wheel --outdir "$WORK/dist" "$ROOT" >/dev/null
WHEEL="$(ls "$WORK"/dist/*.whl)"
echo "    $(basename "$WHEEL")"

echo "==> installing into a fresh virtualenv with [$EXTRAS]"
"$PYTHON" -m venv "$WORK/venv"
"$WORK/venv/bin/python" -m pip install -q --upgrade pip
"$WORK/venv/bin/python" -m pip install -q "${WHEEL}[${EXTRAS}]"
if [[ "$EXTRAS" == *playwright* || "$EXTRAS" == *all* ]]; then
    "$WORK/venv/bin/python" -m playwright install chromium >/dev/null
fi
"$WORK/venv/bin/headless-driver" --version

echo "==> running the end-to-end suite against the installed package"
cp -R "$ROOT/tests/e2e" "$WORK/e2e"
cd "$WORK"
E2E_REQUIRE_INSTALLED=1 "$WORK/venv/bin/python" -m unittest discover -s "$WORK/e2e" -t "$WORK/e2e" -v
