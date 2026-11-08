"""Shared test options and the pinned real-door build for required interop."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption(
        "--require-interop",
        action="store_true",
        help="build and require the pinned Rust door for interop (needs Cargo and curl)",
    )


def pytest_sessionstart(session):
    """Use the merged feed host when CI requires interop, before collecting tests."""
    if not session.config.getoption("--require-interop"):
        return
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "install-interop-door.sh"
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    build = root / ".interop-build"
    binary = build / "tools" / "bin" / "ikigai"
    stamp = build / "door-source.sha256"
    if not binary.is_file() or not stamp.is_file() or stamp.read_text() != digest:
        env = os.environ.copy()
        env.setdefault("CARGO_BUILD_JOBS", "2")
        subprocess.run([str(script)], cwd=root, env=env, check=True, timeout=1800)
        stamp.write_text(digest)
    os.environ["PATH"] = str(binary.parent) + os.pathsep + os.environ.get("PATH", "")
