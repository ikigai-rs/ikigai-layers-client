"""The ``ikigai-layers`` command against a REAL Rust layer door on loopback.

Skips without ``ikigai`` on PATH, and FAILS instead under ``--require-interop`` (CI's
interop job). The door is scratch (``tests/layer_door.py``); nothing here reaches any other.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import io
import json
import ssl
import subprocess
import sys
import threading
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from ikigai_layers_client import certs, feeds, join, message, work
from ikigai_layers_client import door as door_mod
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


@pytest.fixture
def public_door(request, tmp_path):
    d = LayerDoor(require_ikigai(request), tmp_path / "public-door", public_join=True)
    try:
        yield d
    finally:
        d.stop()


@pytest.fixture
def discovery_host(public_door, tmp_path, monkeypatch):
    """Serve the descriptor and QUIC pin over trusted, loopback-only HTTPS."""
    root = tmp_path / "https"
    documents = root / ".well-known" / "ikigai"
    documents.mkdir(parents=True)
    pem = (public_door.root / "door" / "server.crt").read_text()
    (documents / "server.crt").write_text(pem)
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    (documents / "layers.json").write_text(
        json.dumps(
            {
                "door": public_door.url,
                "join": "urn:layer:join:say",
                "server_cert": {"url": "server.crt", "sha256": pin},
                "public_layers": [{"name": "releases", "type": "ik:Feed", "read_only": True}],
            }
        )
    )
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(certs.NOT_BEFORE)
        .not_valid_after(certs.NOT_AFTER)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    tls_cert, tls_key = root / "https.crt", root / "https.key"
    tls_cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    tls_key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tls_cert, tls_key)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(tls_cert))
    try:
        yield f"https://localhost:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.mark.parametrize("selection", [[], ["releases"]])
def test_open_join_from_https_discovery_reads_a_real_public_feed(
    public_door, discovery_host, tmp_path, capsys, selection
):
    doc, location = join.descriptor(discovery_host)
    assert location == discovery_host + join.DESCRIPTOR
    assert doc["door"] == public_door.url
    # Publish only to the scratch loopback door; the joining identity starts unknown to it.
    with door_mod.connect(public_door.url, public_door.actors["alice"]) as connected:
        posted = connected.sink(
            "urn:layer:releases:say",
            "@prefix ik: <https://ikigai-rs.dev/ns#> . [] a ik:Message ; "
            'ik:body "A public release from the real door." .',
            **{"as": "application/json"},
        )
    entry = json.loads(posted.data)["@id"]
    directory = tmp_path / "new-reader"
    assert not directory.exists()
    code, out, err = run(
        capsys, "join", discovery_host, *selection, "--cert-dir", str(directory), "--json"
    )
    assert code == 0, err
    grants = json.loads(out)["granted"]
    assert set(grants) == {"urn:cap:layer:read:releases", "urn:cap:layer:feed:releases"}
    assert (directory / "config.toml").read_text() == f'door = "{public_door.url}"\n'
    assert (directory / "server.crt").read_bytes() == (
        public_door.root / "door" / "server.crt"
    ).read_bytes()
    # Use only the saved connection configuration, in a new session after joining.
    code, out, err = run(capsys, "read", "releases", "--cert-dir", str(directory))
    assert code == 0, err
    assert entry in out and "A public release from the real door." in out
    code, _, err = run(
        capsys, "say", "releases", "Reader cannot publish.", "--cert-dir", str(directory)
    )
    assert code == 1 and "denied" in err
    code, _, err = run(capsys, "read", "talk", "--cert-dir", str(directory))
    assert code == 1 and "denied" in err


def test_the_door_accepts_a_python_made_certificate_once_enrolled(door, capsys):
    code, out, _ = run(capsys, "me", *door.args("alice"))
    assert code == 0, out
    me = json.loads(out)
    assert me["principal"] == "urn:collab:participant:alice"
    assert me["collaborations"] == ["urn:collab:demo"]
    # The door's banner lists the actor's certificate by its short fingerprint.
    assert door.fingerprints["alice"][:16] in door.log


def test_feed_discovery_and_question_channel_selection_on_the_real_catalog(door, capsys):
    code, out, err = run(capsys, "feeds", *door.args("alice"))
    assert code == 0, err
    assert out.strip() == "feed: releases\n    no entries"
    with door_mod.connect(door.url, door.actors["alice"]) as connected:
        layers = feeds.catalog(connected)
        assert layers["talk"] == {"read", "say"}
        assert layers["tasks"] == {"read"}
        assert feeds.question_layer(connected, "talk") == "talk"
        with pytest.raises(ValueError, match="several writable channels"):
            feeds.question_layer(connected, None)


def test_the_real_feed_definition_and_scheme_have_json_faces(door, capsys):
    with door_mod.connect(door.url, door.actors["alice"]) as connected:
        doc = feeds.definition(connected, "releases")
        assert doc == {
            "layer": "releases",
            "label": "Releases",
            "model": "channel",
            "types": ["Layer", "Feed"],
            "feed": True,
            "public": True,
        }
        assert feeds.is_feed(doc, "releases")
        assert feeds.definition(connected, "talk")["model"] == "channel"
    code, out, err = run(capsys, "feeds", "releases", "--scheme", *door.args("alice-agent"))
    assert code == 0, err
    assert out.splitlines() == [
        "scheme for releases:",
        "Features <urn:release:feature>",
        "    User interface <urn:release:feature:ui>",
    ]


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


# Audit cases use a separate scratch door so their feed history is independent.


@pytest.fixture(scope="module")
def audit_door(request, tmp_path_factory):
    d = LayerDoor(require_ikigai(request), tmp_path_factory.mktemp("audit-door"))
    try:
        yield d
    finally:
        d.stop()


# talk.toml bounds the complete Turtle request to 16 KiB, including its prefixes.
_SAY_BYTES = 16384 - len(message.message("").encode("utf-8"))


@pytest.mark.parametrize(
    "body",
    [
        'two\nlines "quoted" café ☃',
        "\ufeffleading BOM",
        "x" * _SAY_BYTES,
        "é" * (_SAY_BYTES // 2) + "x" * (_SAY_BYTES % 2),
    ],
)
def test_say_read_exact(audit_door, capsys, body):
    assert main(["say", "talk", body, *audit_door.args("alice")]) == 0
    iri = capsys.readouterr().out.strip()
    assert main(["read", "talk", "--json", *audit_door.args("alice")]) == 0
    doc = json.loads(capsys.readouterr().out)
    node = next(n for n in doc["@graph"] if n["@id"] == iri)
    assert node["body"].encode("utf-8") == body.encode("utf-8")


def test_save_does_not_replace_a_file_created_during_download(
    audit_door, capsys, tmp_path, monkeypatch
):
    from ikigai_layers_client import cli

    original = tmp_path / "source.txt"
    original.write_bytes(b"from the layer")
    assert main(["post", "file", "work", str(original), *audit_door.args("alice")]) == 0
    iri = capsys.readouterr().out.strip()
    target = tmp_path / "download.txt"
    mkstemp = cli.tempfile.mkstemp

    def competing_writer(*args, **kwargs):
        # Another save finishes after the existence check but before publication.
        target.write_bytes(b"from the other save")
        return mkstemp(*args, **kwargs)

    monkeypatch.setattr(cli.tempfile, "mkstemp", competing_writer)
    code = main(["save", iri, str(target), *audit_door.args("alice")])
    out, err = capsys.readouterr()
    assert target.read_bytes() == b"from the other save"
    assert code == 2 and "exists" in err and out == ""
    assert not list(tmp_path.glob(".ikigai-layers-*"))


@pytest.mark.parametrize(
    ("body", "code", "reason"), [("", 2, "empty"), ("x" * (_SAY_BYTES + 1), 1, "16384")]
)
def test_say_refusals(audit_door, capsys, body, code, reason):
    assert main(["say", "talk", body, *audit_door.args("alice")]) == code
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("ikigai-layers: ") and reason in err


def test_two_clients_race_to_take_one_offer(audit_door, capsys):
    from concurrent.futures import ThreadPoolExecutor

    from ikigai_layers_client import door as door_mod

    subject = "urn:layer:offers:offer:audit-race"
    turtle = (
        "@prefix ik: <https://ikigai-rs.dev/ns#> . "
        "@prefix dcterms: <http://purl.org/dc/terms/> . "
        f'<{subject}> a ik:Offer ; dcterms:title "Race" ; ik:brief "One winner." ; '
        "ik:repository <https://github.com/example/repo> ."
    )
    with door_mod.connect(audit_door.url, audit_door.actors["alice"]) as d:
        d.sink("urn:layer:offers:tuples", turtle, **{"as": "application/json"})

    barrier = threading.Barrier(2)

    def take(actor):
        barrier.wait(timeout=10)
        return actor, subprocess.run(
            [
                sys.executable,
                "-m",
                "ikigai_layers_client",
                "take",
                "offers",
                subject,
                "--json",
                *audit_door.args(actor),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(take, ["alice", "alice-agent"]))
    winners = [(actor, done) for actor, done in results if done.returncode == 0]
    assert len(winners) == 1, results
    winner, done = winners[0]
    assert any(n["@id"] == subject for n in json.loads(done.stdout)["@graph"])
    loser = next(done for _, done in results if done.returncode != 0)
    assert loser.returncode == 1 and loser.stdout == ""
    assert loser.stderr.startswith("ikigai-layers: conflict:")
    assert f"urn:collab:participant:{winner}" in loser.stderr
    assert main(["tuples", "offers", "--taken", *audit_door.args("alice")]) == 0
    out, err = capsys.readouterr()
    assert subject in out and f"urn:collab:participant:{winner}" in out and not err


def test_follow_resumes_in_order_after_restart(audit_door, capsys, tmp_path):
    import subprocess
    import time

    import ikigai

    from ikigai_layers_client import door as door_mod
    from ikigai_layers_client import follow

    def post(body):
        with door_mod.connect(audit_door.url, audit_door.actors["alice"], timeout=2) as d:
            return json.loads(
                d.sink(
                    "urn:layer:talk:say", message.message(body), **{"as": "application/json"}
                ).data
            )["@id"]

    before = post("before restart")
    lines = []
    f = follow.Follower(
        lambda timeout: door_mod.connect(audit_door.url, audit_door.actors["alice"], timeout=2),
        layers=["talk"],
        state=str(tmp_path / "cursor"),
        wait=1,
        say=lines.append,
        describe=True,
    )
    try:
        assert f.run(once=True) == 0
        assert any(before in line for line in lines)
        cursor = follow.read_state(f.state)
        argv = [audit_door.url if a == "quic://127.0.0.1:0" else a for a in audit_door.process.args]
        audit_door.stop()
        audit_door.process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.monotonic() + 20
        while True:
            try:
                after = post("after restart")
                break
            except ikigai.ConnectionLost:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        # Reuse the connection that the host restart broke, with the saved cursor.
        # Continuous follow retries outages; --once is documented to exit 5 on one.
        deadline = time.monotonic() + 60

        def retry(_delay):
            assert time.monotonic() < deadline, "follow did not resume after the restart"
            time.sleep(0.05)

        f.sleep = retry
        lines.clear()
        assert f.run(max_reads=1) == 0
        events = [line for line in lines if line.startswith("talk ")]
        assert len(events) == 1 and after in events[0]
        assert follow.read_state(f.state)["talk"] == cursor["talk"] + 1
    finally:
        f.close()
    # A new process using the same state must see neither entry again.
    assert (
        main(
            ["follow", "--once", "--layers", "talk", "--state", f.state, *audit_door.args("alice")]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
