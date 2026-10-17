#!/usr/bin/env bash
# One-time local setup (CLAUDE.md section 53). Safe to re-run.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "GhostCash bootstrap"
echo "===================="

# 1. Check Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.12+ first." >&2
    exit 1
fi
PY_VERSION="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')"
echo "[ok] python3 found (version ${PY_VERSION})"

# 1b. Check ffmpeg (needed for video rendering, Phase 4). The system
# package (apt-get install ffmpeg on Ubuntu/Debian) includes the
# libflite support GhostCash uses for free/local text-to-speech.
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "WARNING: ffmpeg not found. \`ghostcash render\` needs it." >&2
    echo "         Install it with: sudo apt-get install -y ffmpeg fonts-dejavu-core" >&2
else
    echo "[ok] ffmpeg found ($(ffmpeg -version | head -n1))"
fi

# 2. Create virtual environment
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "[ok] created virtual environment at .venv"
else
    echo "[skip] .venv already exists"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# 3. Install dependencies
pip install --upgrade pip >/dev/null
pip install -e ".[dev]"
echo "[ok] installed dependencies"

# 4-6. Create database, apply migrations, create .env/config.yaml from examples
python3 -m ghostcash.cli.main init

# 7. Run tests
pytest

# 8. Run health check (non-fatal: a fresh install may still need API keys)
ghostcash health || true

# 9. Print remaining manual setup steps
cat <<'MSG'

Remaining manual setup steps:
  1. Edit .env and set AI_API_KEY (see README.md "External accounts").
  2. Add BUFFER_API_KEY once you reach the publishing phase.
  3. Add GUMROAD_API_KEY once you reach the product phase.
  4. Review config/config.yaml for scoring weights, budgets, and approval modes.
MSG
