#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPINE_PYTHON="${SPINE_PYTHON:-$HOME/miniconda3/envs/spine/bin/python}"
if [[ ! -x "$SPINE_PYTHON" ]]; then
    echo "Spine Python missing: $SPINE_PYTHON. See README.md for installation." >&2
    exit 1
fi
export CONDA_DEFAULT_ENV=spine
export CONDA_PREFIX="$(dirname "$(dirname "$SPINE_PYTHON")")"
export PATH="$(dirname "$SPINE_PYTHON"):$PATH"
export PYTHONNOUSERSITE=1
cd "$PROJECT_DIR"
exec "$SPINE_PYTHON" gui.py "$@"
