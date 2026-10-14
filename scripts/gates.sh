#!/usr/bin/env bash
# The gates CI runs, in CI's order: lint, format check, tests.
#
#   scripts/gates.sh [python]
#
# ONE source of truth: .github/workflows/ci.yml calls this after its Install step, and the
# justfile's `gates` recipe calls it locally (which is what
# `just -f ~/git-personal/ikigai-devtools/justfile gates` runs in a repo with no Cargo.toml).
#
# Every tool runs through ONE interpreter (`python -m ruff`, `python -m pytest`). Without an
# argument that is .venv/bin/python, else python3. It must import this checkout's package and
# have the dev extra, or the gates REFUSE to run rather than cover less than CI does.
#
# The interop tests (tests/test_interop.py) need the Rust `ikigai` on PATH and SKIP without
# it; CI's `interop` job runs them with --require-interop, where a missing binary fails.

set -u
cd "$(dirname "$0")/.." || exit 2

if [ $# -gt 0 ]; then
    PY="$1"
elif [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
else
    PY=python3
fi

if ! "$PY" - <<'PYEOF'; then
import importlib.util
import sys
from pathlib import Path

wanted = ("pytest", "ruff", "ikigai_layers_client")
missing = [m for m in wanted if importlib.util.find_spec(m) is None]
if missing:
    sys.exit("missing: " + ", ".join(missing))
import ikigai_layers_client

src = (Path.cwd() / "src").resolve()
if src not in Path(ikigai_layers_client.__file__).resolve().parents:
    sys.exit(f"imports ikigai_layers_client from {ikigai_layers_client.__file__}, not here")
PYEOF
    echo "gates: $PY cannot run the gates: NOTHING WAS GATED" >&2
    echo "  install with: $PY -m pip install -e '.[dev]'" >&2
    exit 2
fi

echo "gates: $PY ($("$PY" --version 2>&1))"
echo "── ruff check" &&
    "$PY" -m ruff check . &&
    echo "── ruff format --check" &&
    "$PY" -m ruff format --check . &&
    echo "── pytest" &&
    "$PY" -m pytest -q
