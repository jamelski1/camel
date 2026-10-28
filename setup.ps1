# One-shot environment setup for SENG 691 Group 3 - CAMEL Phase 1 (Windows).
# Same four steps as setup.sh; only the venv interpreter path differs.
# Requires: git, and uv (https://docs.astral.sh/uv/) on PATH.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host "[1/4] Fetching CAMEL source (git submodule, pinned commit)..."
git submodule update --init --depth 1 camel

Write-Host "[2/4] Ensuring Python 3.10 is available (CAMEL CI uses 3.10)..."
uv python install 3.10

Write-Host "[3/4] Creating virtual environment .venv ..."
uv venv --python 3.10 .venv

Write-Host "[4/4] Installing CAMEL from source in editable mode..."
uv pip install --python .venv\Scripts\python.exe -e .\camel

Write-Host ""
Write-Host "Done. Next steps:"
Write-Host "  .venv\Scripts\Activate.ps1"
Write-Host "  python demo\check_env.py"
Write-Host "  Copy-Item .env.example .env   # then put a real OPENAI_API_KEY in .env"
Write-Host "  python demo\role_playing_demo.py --turns 5"
