#!/usr/bin/env bash
# Install the published real door; tests/conftest.py reuses it by this script's digest.
set -euo pipefail
cargo install --locked ikigai-cli --version 0.4.1 --features quic --root .interop-build/tools
