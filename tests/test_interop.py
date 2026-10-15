"""The ``ikigai-layers`` command against a REAL Rust layer door on loopback.

Skips without ``ikigai`` on PATH, and FAILS instead under ``--require-interop`` (CI's
interop job). The door is scratch (``tests/layer_door.py``); nothing here reaches any other.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest

from ikigai_layers_client.cli import main
from tests.layer_door import LayerDoor, require_ikigai


@pytest.fixture(scope="module")
def door(request, tmp_path_factory):
    ikigai = require_ikigai(request)
    d = LayerDoor(ikigai, tmp_path_factory.mktemp("door"))
    yield d
    d.stop()


def run(capsys, *argv, stdin: str | None = None):
    if stdin is not None:
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
    try:
        code = main(list(argv))
    finally:
        if stdin is not None:
            sys.stdin = old
    out, err = capsys.readouterr()
    return code, out, err


def test_the_door_accepts_a_python_made_certificate_once_enrolled(door, capsys):
    code, out, _ = run(capsys, "me", *door.args("alice"))
    assert code == 0, out
    me = json.loads(out)
    assert me["principal"] == "urn:collab:participant:alice"
    assert me["collaborations"] == ["urn:collab:demo"]
    # The door's banner lists the actor's certificate by its short fingerprint.
    assert door.fingerprints["alice"][:16] in door.log


def test_the_same_set_works_with_the_rust_cli_unchanged(door):
    done = subprocess.run(
        [
            "ikigai",
            "--override",
            f"urn:layer:,urn:layers:,urn:collab:={door.url}",
            "--cert-dir",
            str(door.actors["alice"]),
            "-c",
            "source urn:layers:me as=application/json",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert '"principal":"urn:collab:participant:alice"' in done.stdout.replace(" ", "")


def test_an_unenrolled_certificate_is_refused_naming_its_fingerprint(door, capsys):
    code, _, err = run(capsys, "me", *door.args("outsider"))
    assert code == 4
    assert "has not enrolled it" in err and door.fingerprints["outsider"] in err


def test_say_then_read_then_follow(door, capsys, tmp_path):
    code, out, _ = run(capsys, "say", "talk", "Hello from Python.", *door.args("alice"))
    assert code == 0 and out.startswith("urn:layer:talk:entry:")
    said = out.strip()
    code, out, _ = run(
        capsys,
        "say",
        "talk",
        "-",
        "--in-reply-to",
        said,
        *door.args("alice-agent"),
        stdin="Two lines,\nfrom stdin.\n",
    )
    assert code == 0, out
    code, out, _ = run(capsys, "read", "talk", *door.args("alice-agent"))
    assert code == 0
    assert "urn:collab:participant:alice Message " + said in out
    assert "    Hello from Python." in out
    assert f"    in reply to {said}" in out
    assert "    Two lines,\n    from stdin.\n" in out  # the piped newline was not kept
    state = tmp_path / "agent.cursor"
    code, out, _ = run(capsys, "follow", "--once", "--state", str(state), *door.args("alice-agent"))
    assert code == 0
    lines = out.splitlines()
    # The door's six fields, then what the entry is (read from the entry itself).
    assert lines[0].startswith("talk 1 ") and lines[0].endswith(
        "say " + said + ' urn:collab:participant:alice Message "Hello from Python."'
    )
    assert state.read_text().startswith("talk:")
    # A second --once read prints nothing new.
    code, out, _ = run(capsys, "follow", "--once", "--state", str(state), *door.args("alice-agent"))
    assert code == 0 and out == ""


def test_follow_stops_on_a_layer_it_holds_no_feed_on(door, capsys, tmp_path):
    code, out, _ = run(
        capsys,
        "follow",
        "--once",
        "--layers",
        "tasks",
        "--state",
        str(tmp_path / "c"),
        *door.args("alice-agent"),
    )
    assert code == 4 and "no feed grant on layer `tasks`" in out


def test_follow_stops_on_a_stale_cursor(door, capsys, tmp_path):
    (tmp_path / "c").write_text("talk:999\n")
    code, out, _ = run(
        capsys, "follow", "--once", "--state", str(tmp_path / "c"), *door.args("alice")
    )
    assert code == 3 and "--reset" in out


def test_out_tuples_take_and_a_second_take_is_a_conflict(door, capsys):
    offer = (
        "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
        "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
        '<urn:layer:tasks:offer:interop> a ik:Offer ; dcterms:title "Write the client" .\n'
    )
    code, out, _ = run(capsys, "out", "tasks", *door.args("alice"), stdin=offer)
    assert (code, out.strip()) == (0, "urn:layer:tasks:offer:interop")
    code, out, _ = run(capsys, "tuples", "tasks", *door.args("alice"))
    assert code == 0 and "urn:layer:tasks:offer:interop urn:collab:participant:alice" in out
    code, out, _ = run(
        capsys, "take", "tasks", "urn:layer:tasks:offer:interop", *door.args("alice")
    )
    assert code == 0
    taken = json.loads(out)
    assert taken[0]["title"] == "Write the client"
    code, _, err = run(
        capsys, "take", "tasks", "urn:layer:tasks:offer:interop", *door.args("alice")
    )
    assert code == 1 and "already taken by <urn:collab:participant:alice>" in err
    code, out, _ = run(capsys, "tuples", "tasks", "--taken", *door.args("alice"))
    assert code == 0 and "urn:layer:tasks:receipt:" in out


def test_a_layer_this_actor_holds_nothing_on_is_denied_by_name(door, capsys):
    code, _, err = run(capsys, "tuples", "tasks", *door.args("alice-agent"))
    assert code == 1 and "denied" in err and "urn:cap:layer:read:tasks" in err


def test_an_unreachable_door_exits_5(door, capsys):
    code, _, err = run(
        capsys, "me", "--door", "quic://127.0.0.1:9", "--cert-dir", str(door.actors["alice"])
    )
    assert code == 5 and "unreachable" in err
