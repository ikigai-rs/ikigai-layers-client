"""The ``ikigai-layers`` command against a REAL Rust layer door on loopback.

Skips without ``ikigai`` on PATH, and FAILS instead under ``--require-interop`` (CI's
interop job). The door is scratch (``tests/layer_door.py``); nothing here reaches any other.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time

import pytest

from ikigai_layers_client import door as door_mod
from ikigai_layers_client import work
from ikigai_layers_client.cli import main
from tests.layer_door import LayerDoor, require_ikigai
from tests.test_agent_loop import (  # noqa: F401 - `world` is a fixture
    AGENT,
    ALICE,
    REPOSITORY,
    pull_request,
    world,
)


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


# -- `work`: the agent loop, with real takes, receipts, shapes and grants ---------------------
#
# The agent, `gh` and the git remote stay fake (tests/test_agent_loop.py), so nothing leaves
# loopback. The `offers` layer has the offer and report shapes ikigai-layers' own tests use, with
# `addressed` on.


def drop(door, name, *, repository=REPOSITORY):
    subject = f"urn:layer:offers:offer:{name}"
    turtle = (
        "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
        "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
        f'<{subject}> a ik:Offer ; dcterms:title "Task {name}" ; ik:brief "Do {name}." ; '
        f"ik:repository <{repository}> .\n"
    )
    with door_mod.connect(door.url, door.actors["alice"]) as d:
        d.sink("urn:layer:offers:tuples", turtle, **{"as": "application/json"})
    return subject


def agent_worker(world, door, **changes):  # noqa: F811
    changes.setdefault("layer", "offers")
    config = world.config(**changes)
    return work.Worker(
        config,
        lambda timeout: door_mod.connect(door.url, door.actors["alice-agent"], timeout=timeout),
        state_dir=world.root / "state",
        say=world.lines.append,
    )


def pending_for_alice(door) -> dict[str, dict]:
    with door_mod.connect(door.url, door.actors["alice"]) as d:
        answer = d.source("urn:layer:offers:tuples", **{"as": "application/ld+json"})
    return {n["@id"]: n for n in json.loads(answer.data).get("@graph", [])}


def finish(door, subjects):
    """Take what alice owes herself, so each test starts from an empty layer."""
    with door_mod.connect(door.url, door.actors["alice"]) as d:
        for subject in subjects:
            d.delete("urn:layer:offers:tuples", tuple=subject, **{"as": "application/ld+json"})


def test_a_real_door_takes_the_offer_and_admits_the_hand_back(world, door):  # noqa: F811
    subject = drop(door, "one")
    world.github(prs=[pull_request()], checks=("pass",))
    outcome = agent_worker(world, door).run_once()
    assert outcome.code == work.EXIT_DONE, (outcome.summary, world.lines)
    assert "# Task one\n\nDo one." in world.seen()["prompt"]

    reports = {
        s: n
        for s, n in pending_for_alice(door).items()
        if one_type(n) in ("Handback", "Progress", "Release")
    }
    [handback] = [n for n in reports.values() if one_type(n) == "Handback"]
    assert handback["offer"] == subject and handback["addressedTo"] == ALICE
    assert handback["attributedTo"] == AGENT, "the host stamps who reported"
    assert handback["evidence"] == f"{REPOSITORY}/pull/7"
    assert "CI green" in handback["note"]
    finish(door, reports)


def one_type(node):
    types = node.get("@type")
    return types if isinstance(types, str) else (types or [None])[0]


def test_a_real_door_admits_a_release(world, door, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENT_DOES", "fail")
    subject = drop(door, "two")
    assert agent_worker(world, door).run_once().code == work.EXIT_NOT_HANDED_BACK
    reports = {
        s: n
        for s, n in pending_for_alice(door).items()
        if one_type(n) in ("Release", "Progress") and n.get("offer") == subject
    }
    assert [n for n in reports.values() if one_type(n) == "Release"]
    finish(door, reports)


def test_the_loop_wakes_on_the_real_feed_and_works_an_offer_dropped_later(world, door, tmp_path):  # noqa: F811
    world.github(prs=[pull_request(8)])
    worker = agent_worker(world, door)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(worker.loop(tmp_path / "cursor", wait=1)), daemon=True
    )
    thread.start()
    time.sleep(1.5)  # it has read the empty layer, and is waiting on the feed
    subject = drop(door, "later")
    deadline = time.monotonic() + 60
    found = None
    while found is None and time.monotonic() < deadline:
        pending = pending_for_alice(door).values()
        found = next((n for n in pending if one_type(n) == "Handback"), None)
        time.sleep(0.2)
    worker.shutdown.set()
    thread.join(timeout=30)
    assert result == [work.EXIT_DONE], world.lines
    assert found is not None and found["offer"] == subject
    finish(
        door,
        [s for s, n in pending_for_alice(door).items() if n.get("offer") == subject],
    )


def test_a_door_that_grants_less_stops_the_loop_by_name(world, door, capsys, monkeypatch):  # noqa: F811
    # alice-agent holds nothing on `tasks`: the door's denial stops it, and nothing else is tried.
    with pytest.raises(Exception, match="does not grant") as caught:
        agent_worker(world, door, layer="tasks").run_once()
    assert "denied" in caught.value.display()
    assert not world.record.exists()


def test_the_door_is_who_says_which_actor_this_is(world, door):  # noqa: F811
    drop(door, "three")
    with pytest.raises(work.Refused, match="Use the agent's own certificate"):
        agent_worker(world, door, actor=ALICE).run_once()
    assert not world.record.exists()
    # Left pending, for this test's own cleanup: alice withdraws it by taking it.
    finish(door, ["urn:layer:offers:offer:three"])


def test_plan_reads_the_real_listing_and_takes_nothing(world, door):  # noqa: F811
    good = drop(door, "four")
    worker = agent_worker(world, door)
    assert work.plan(worker) == work.EXIT_DONE
    assert f"would take {good}" in world.lines
    assert good in pending_for_alice(door)
    finish(door, [good])
