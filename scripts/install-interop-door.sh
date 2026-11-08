#!/usr/bin/env bash
# Build a real CLI door with the merged feed JSON faces, before their crates.io release.
set -euo pipefail
CLI_REV=3b54f02dcde4c7d5cdf06229cdcd01e80d82c0d1
LAYERS_REV=3c6ae61b9d8b2572aae9eb52f63250a6f89d48da
BUILD=.interop-build
mkdir -p "$BUILD"
curl -fsSL "https://github.com/ikigai-rs/ikigai-cli/archive/$CLI_REV.tar.gz" | tar -xz -C "$BUILD"
SOURCE="$BUILD/ikigai-cli-$CLI_REV"
python3 - "$SOURCE/Cargo.toml" "$LAYERS_REV" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = 'ikigai-layers = "0.7.1"'
assert s.count(old) == 1
p.write_text(s.replace(old, 'ikigai-layers = { git = "https://github.com/ikigai-rs/ikigai-layers", rev = "' + sys.argv[2] + '" }'))
PY
cp "$SOURCE/Cargo.lock" "$BUILD/original.lock"
cargo update --manifest-path "$SOURCE/Cargo.toml" -p ikigai-layers
# Keep the CLI's exact transitive lock (notably spareval's digest 0.11 hashes).
# Updating layers alone otherwise unifies md-5 with steel's incompatible 0.10.
python3 - "$SOURCE/Cargo.lock" "$BUILD/original.lock" <<'PYLOCK'
from pathlib import Path
import sys
p = Path(sys.argv[1])
original = Path(sys.argv[2]).read_text()
updated = p.read_text()
def layer_block(text):
    return next(b for b in text.split("[[package]]") if '\nname = "ikigai-layers"\n' in b)
p.write_text(original.replace(layer_block(original), layer_block(updated)))
PYLOCK
cargo install --path "$SOURCE/crates/ikigai-cli" --locked --features quic --root "$BUILD/tools"
