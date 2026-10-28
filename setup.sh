#!/usr/bin/env bash
# One-shot environment setup for SENG 691 Group 3 - CAMEL Phase 1.
# Reproduces exactly the steps recorded in docs/SETUP_NOTES.md.
# Requires: git, and uv (https://docs.astral.sh/uv/) on PATH.
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] Fetching CAMEL source (git submodule, pinned commit)..."
git submodule update --init --depth 1 camel

echo "[2/4] Ensuring Python 3.10 is available (CAMEL CI uses 3.10)..."
uv python install 3.10

echo "[3/4] Creating virtual environment .venv ..."
uv venv --python 3.10 .venv

echo "[4/4] Installing CAMEL from source in editable mode..."
uv pip install --python .venv/bin/python -e ./camel

echo
echo "Done. Next steps:"
echo "  source .venv/bin/activate"
echo "  python demo/check_env.py"
echo "  cp .env.example .env   # then put a real OPENAI_API_KEY in .env"
echo "  python demo/role_playing_demo.py --turns 5"
