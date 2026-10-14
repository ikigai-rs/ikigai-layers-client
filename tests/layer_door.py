"""A real Rust layer door on loopback for the interop tests: ``ikigai serve quic://`` with a
``layers/`` config home, as the edge's layer door runs (its unit's ExecStart), but scratch.

Port 0 on 127.0.0.1 (the door binds first and names its port in the banner), the door's
cert set from ``ikigai cert generate``, and the actors' sets from THIS package's
``cert generate``: each actor's ``client.crt`` goes into the door's trust list
(``clients/<actor>.crt``) and, unless it is meant to stay unenrolled, its fingerprint into
``clients.json``. Stopped by its PID.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from ikigai_layers_client import certs

IKIGAI = shutil.which("ikigai")
BANNER = re.compile(r"serving on (quic://127\.0\.0\.1:\d+)")
FIXTURES = Path(__file__).parent / "fixtures" / "layers"

# Each enrolled actor's grant: the scopes `ikigai-layers grants` derives from
# fixtures/layers/collabs/demo.toml, plus the collaboration's read.
GRANTS = {
    "alice": [
        "urn:cap:collab:read:demo",
        "urn:cap:layer:feed:talk",
        "urn:cap:layer:feed:tasks",
        "urn:cap:layer:out:tasks",
        "urn:cap:layer:read:talk",
        "urn:cap:layer:read:tasks",
        "urn:cap:layer:say:talk",
        "urn:cap:layer:take:tasks",
    ],
    "alice-agent": [
        "urn:cap:collab:read:demo",
        "urn:cap:layer:feed:talk",
        "urn:cap:layer:read:talk",
        "urn:cap:layer:say:talk",
    ],
}


def require_ikigai(request) -> str:
    """The ``ikigai`` binary, or skip; under ``--require-interop`` a missing one FAILS."""
    if IKIGAI is None:
        if request.config.getoption("--require-interop"):
            pytest.fail("--require-interop, and there is no `ikigai` on PATH")
        pytest.skip("no `ikigai` binary on PATH (the interop tests run against the Rust door)")
    return IKIGAI


class LayerDoor:
    """The door, plus one cert set per actor under :attr:`actors` (``name -> dir``)."""

    def __init__(self, ikigai: str, root: Path, *, unenrolled=("outsider",)):
        self.root = root
        door_set = root / "door"
        done = subprocess.run(
            [ikigai, "cert", "generate", "--dir", str(door_set)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if done.returncode != 0:
            pytest.fail(f"`ikigai cert generate` failed (built without `quic`?): {done.stderr}")
        (door_set / "clients").mkdir()
        home = root / "home"
        shutil.copytree(FIXTURES, home)
        self.actors: dict[str, Path] = {}
        self.fingerprints: dict[str, str] = {}
        clients = {}
        for name in [*GRANTS, *unenrolled]:
            actor = root / "actors" / name
            self.fingerprints[name] = certs.generate(actor)
            shutil.copy(door_set / "server.crt", actor / "server.crt")
            shutil.copy(actor / "client.crt", door_set / "clients" / f"{name}.crt")
            self.actors[name] = actor
            if name in GRANTS:
                clients[self.fingerprints[name]] = {
                    "grant": name,
                    "principal": f"urn:collab:participant:{name}",
                    "label": name,
                }
        (home / "clients.json").write_text(json.dumps({"clients": clients}, indent=2))
        (home / "grants.json").write_text(json.dumps(GRANTS, indent=2))
        for d in ("files", "state"):
            (root / d).mkdir()
        argv = [
            ikigai,
            "--config-home",
            str(home),
            "--files",
            str(root / "files"),
            "serve",
            "quic://127.0.0.1:0",
            "--name",
            "layers",
            "--cert-dir",
            str(door_set),
            "--layers-state",
            str(root / "state"),
        ]
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.lines: list[str] = []
        self.url: str | None = None
        found = threading.Event()

        def drain():
            for line in self.process.stdout:
                self.lines.append(line.rstrip("\n"))
                m = BANNER.search(line)
                if m and self.url is None:
                    self.url = m.group(1)
                    found.set()
            found.set()

        threading.Thread(target=drain, daemon=True).start()
        deadline = time.monotonic() + 30
        while not found.wait(0.1) and time.monotonic() < deadline:
            pass
        if self.url is None:
            self.stop()
            pytest.fail("the layer door did not come up:\n" + self.log)

    @property
    def log(self) -> str:
        """Everything the door printed so far."""
        return "\n".join(self.lines)

    def args(self, actor: str) -> list[str]:
        """The connection arguments for ``actor``: ``--door … --cert-dir …``."""
        return ["--door", self.url, "--cert-dir", str(self.actors[actor])]

    def stop(self) -> None:
        """Terminate the door by its PID and reap it."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
