"""Enrollment from a Python-only machine, with the starter's wire contract."""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import ikigai
import pytest
from ikigai import quic

from ikigai_layers_client import certs, door, onboard
from ikigai_layers_client.cli import main


def test_onboard_is_available(capsys):
    try:
        main(["onboard", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "--invite" in capsys.readouterr().out


@pytest.fixture
def lobby(tmp_path, monkeypatch):
    server, _, pin = certs.identity()
    server_path = tmp_path / "door.crt"
    server_path.write_text(server, encoding="utf-8")
    directory = tmp_path / "node"
    monkeypatch.setattr(door, "CERT_ROOT", tmp_path / "actors")

    class Lobby:
        def __init__(self):
            self.posts = []
            self.reads = []
            self.connections = []
            self.grants = None
            self.post_error = None
            self.closed = 0

        def connect(self, url, cert_dir, **kwargs):
            self.connections.append((url, cert_dir))
            loaded = quic.CertSet.from_dir(cert_dir).load()
            self.fingerprint = loaded.fingerprint
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed += 1

        def sink(self, iri, content, **kwargs):
            assert iri == "urn:layer:enroll:say"
            assert kwargs == {"as": "application/json"}
            if self.post_error:
                raise self.post_error
            self.posts.append(content)
            return SimpleNamespace(data=b'{"@id":"urn:layer:enroll:entry:request1"}')

        def source(self, iri, **kwargs):
            if iri == "urn:layers:me":
                return SimpleNamespace(data=b'{"principal":"urn:collab:participant:mini"}')
            assert iri == "urn:layer:grants:entries"
            assert kwargs["type"] == onboard.ONB + "Entitlement"
            assert kwargs["as"] == "application/ld+json"
            self.reads.append(kwargs)
            if self.grants is None:
                raise ikigai.DeniedError("not enrolled")
            nodes = self.grants(self) if callable(self.grants) else self.grants
            nodes = [n for n in nodes if int(n.get("seq", 1)) > int(kwargs["after"])]
            return SimpleNamespace(data=json.dumps({"@graph": nodes[: int(kwargs["limit"])]}))

        def argv(self, *extra):
            return [
                "onboard",
                "--door",
                "example.test:4434",
                "--cert-dir",
                str(directory),
                "--server-cert",
                str(server_path),
                "--pin",
                pin,
                "--invite",
                "inv-1",
                "--code",
                "K7Q2M9XW4P",
                *extra,
            ]

    fake = Lobby()
    monkeypatch.setattr(door, "connect", fake.connect)
    fake.directory, fake.pin, fake.server_path = directory, pin, server_path
    return fake


def grant(fp, **extra):
    return {
        "@id": "urn:grant:1",
        "@type": "Entitlement",
        "fingerprint": fp,
        "layerGrant": [{"@id": "urn:cap:layer:read:tasks"}, {"@id": "urn:cap:layer:take:tasks"}],
        **extra,
    }


def test_starter_shared_proof_vector_and_exact_request_bytes():
    vector = json.loads(
        (Path(__file__).parent / "fixtures/onboard/starter-vector.json").read_text()
    )
    assert onboard.proof(vector["code"], vector["fingerprint"]) == vector["proof"]
    assert (
        onboard.request_turtle(
            vector["invite"], vector["certificate"], vector["fingerprint"], vector["proof"]
        )
        == vector["request"]
    )


def test_generate_post_resume_and_configure_without_external_programs(lobby, monkeypatch, capsys):
    # Any certificate generation, hashing, or setup via a process or a symlink would fail.
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(os, "symlink", lambda *a, **kw: pytest.fail("requires symlinks"))
    assert main(lobby.argv("--once")) == 3
    directory = lobby.directory
    original_key = (directory / "client.key").read_bytes()
    fp = quic.fingerprint_of_pem((directory / "client.crt").read_text())
    assert lobby.posts == [
        onboard.request_turtle(
            "inv-1", (directory / "client.crt").read_text(), fp, onboard.proof("K7Q2M9XW4P", fp)
        )
    ]
    assert not (directory / "config.toml").exists()
    assert main(lobby.argv("--once")) == 3
    assert len(lobby.posts) == 1
    lobby.grants = [grant(fp)]
    assert main(lobby.argv("--once")) == 0
    assert (directory / "client.key").read_bytes() == original_key
    assert onboard.configured_door(directory) == "quic://example.test:4434"
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert main(lobby.argv("--once")) == 0
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    assert len(lobby.posts) == 1 and lobby.closed == len(lobby.connections)
    output = capsys.readouterr()
    assert "not enrolled yet" in output.out and "enrolled:" in output.out
    assert "K7Q2M9XW4P" not in output.out + output.err
    assert "PRIVATE KEY" not in output.out + output.err
    assert all(b"K7Q2M9XW4P" not in p.read_bytes() for p in directory.iterdir())
    assert not (directory / "server.key").exists()
    if os.name != "nt":
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((directory / "client.key").stat().st_mode) == 0o600
        assert stat.S_IMODE((directory / "onboard.json").stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "option,value,code",
    [
        ("--pin", "00" * 32, 4),
        ("--pin", "bad", 2),
        ("--invite", "inv> . <injection", 2),
        ("--code", "has spaces", 2),
        ("--max-wait", "nan", 2),
        ("--max-wait", "-1", 2),
        ("--door", "https://example.test", 2),
    ],
)
def test_invalid_inputs_never_dial(lobby, option, value, code, capsys):
    assert main(lobby.argv(option, value, "--once")) == code
    assert not lobby.connections and not lobby.posts
    assert not lobby.directory.exists()
    assert capsys.readouterr().err.startswith("ikigai-layers: ")


@pytest.mark.parametrize("part", ["client.key", "client.crt"])
def test_half_an_identity_is_refused_without_changing_it(lobby, part):
    lobby.directory.mkdir()
    (lobby.directory / part).write_text("keep me")
    assert main(lobby.argv("--once")) == 2
    assert (lobby.directory / part).read_text() == "keep me"
    assert not lobby.connections


def test_existing_pair_must_match(lobby):
    certs.generate(lobby.directory)
    _, other_key, _ = certs.identity()
    (lobby.directory / "client.key").write_text(other_key)
    assert main(lobby.argv("--once")) == 2
    assert not lobby.connections


def test_new_invite_or_door_posts_again(lobby):
    assert main(lobby.argv("--once")) == 3
    assert main(lobby.argv("--invite", "inv-2", "--once")) == 3
    assert main(lobby.argv("--invite", "inv-2", "--door", "other.test:4434", "--once")) == 3
    assert len(lobby.posts) == 3


def test_request_refusal_is_reported_without_recording_success(lobby, capsys):
    lobby.post_error = ikigai.DeniedError("request refused")
    assert main(lobby.argv("--once")) == 1
    assert "denied: request refused" in capsys.readouterr().err
    assert not (lobby.directory / "onboard.json").exists()
    assert not lobby.reads


def test_backoff_and_deadline_reconnect_for_new_authorization(lobby, monkeypatch):
    clock, sleeps = [0], []
    monkeypatch.setattr(onboard.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(onboard.time, "sleep", sleep)
    assert main(lobby.argv("--max-wait", "1000")) == 3
    assert sleeps == [5, 10, 20, 40, 80, 160, 300, 300, 85]
    assert len(lobby.connections) == len(lobby.reads) + 1
    assert len(lobby.posts) == 1


def test_approval_after_a_poll(lobby, monkeypatch):
    monkeypatch.setattr(
        onboard.time, "sleep", lambda _: setattr(lobby, "grants", [grant(lobby.fingerprint)])
    )
    assert main(lobby.argv()) == 0
    assert len(lobby.posts) == 1 and len(lobby.reads) == 2


@pytest.mark.parametrize("form", ["bare", "prefixed", "full"])
def test_entitlement_spellings_and_ik_supersedes(form):
    fp = "ab" * 32
    old = grant(fp)
    new = {
        "@id": "urn:grant:2",
        "@type": "Entitlement",
        "fingerprint": fp,
        "supersedes": {"@id": "urn:grant:1"},
    }
    if form != "bare":
        ns = "onb:" if form == "prefixed" else onboard.ONB
        ik = "ik:" if form == "prefixed" else onboard.IK
        for node in (old, new):
            node["@type"] = [ns + "Entitlement"]
            for key in ("fingerprint", "layerGrant"):
                if key in node:
                    node[ns + key] = node.pop(key)
        new[ik + "supersedes"] = new.pop("supersedes")
    assert onboard.current([old, new, grant("cd" * 32)], fp, datetime.now(UTC)) == [new]


@pytest.mark.parametrize("expiry", ["2000-01-01T00:00:00Z", "bad", "2999-01-01T00:00:00"])
def test_expired_or_invalid_replacement_does_not_resurrect_old_grant(expiry):
    old = grant("ab" * 32)
    new = grant(
        "ab" * 32,
        **{
            "@id": "urn:grant:2",
            "expires": {"@value": expiry},
            "ik:supersedes": {"@id": old["@id"]},
        },
    )
    assert onboard.current([old, new], "ab" * 32, datetime.now(UTC)) == []


def test_current_revocation_never_writes_config(lobby, capsys):
    def revoked(d):
        return [
            grant(d.fingerprint),
            {
                "@id": "urn:grant:2",
                "@type": "onb:Entitlement",
                "fingerprint": d.fingerprint,
                "ik:supersedes": {"@id": "urn:grant:1"},
            },
        ]

    lobby.grants = revoked
    assert main(lobby.argv("--once")) == 1
    assert not (lobby.directory / "config.toml").exists()
    assert capsys.readouterr().err.startswith("ikigai-layers: denied: current entitlement")


def test_all_grant_pages_are_read_before_deciding(lobby, monkeypatch):
    monkeypatch.setattr(onboard, "PAGE", 1)
    lobby.grants = lambda d: [
        grant(d.fingerprint, seq=1),
        {
            "@id": "urn:grant:2",
            "@type": "Entitlement",
            "fingerprint": d.fingerprint,
            "ik:supersedes": "urn:grant:1",
            "seq": 2,
        },
    ]
    assert main(lobby.argv("--once")) == 1
    assert [a["after"] for a in lobby.reads] == ["0", "1", "2"]
    assert not (lobby.directory / "config.toml").exists()


@pytest.mark.parametrize("cap", ["urn:old:grant", {"layer": "tasks", "verb": "take"}])
def test_old_nested_grants_and_invalid_capability_iris_are_refused(lobby, cap):
    lobby.grants = lambda d: [grant(d.fingerprint, layerGrant=cap)]
    assert main(lobby.argv("--once")) == 2
    assert not (lobby.directory / "config.toml").exists()


def test_config_is_kept_without_force_and_used_by_other_commands(lobby):
    lobby.grants = lambda d: [grant(d.fingerprint)]
    assert main(lobby.argv("--once")) == 0
    config = lobby.directory / "config.toml"
    config.write_text('door = "quic://other.test:4434"\n')
    assert main(lobby.argv("--once")) == 2
    assert "other.test" in config.read_text()
    assert main(lobby.argv("--force", "--once")) == 0
    # A normal command resolves the enrolled door; an explicit option overrides it.
    assert main(["me", "--cert-dir", str(lobby.directory)]) == 0
    assert lobby.connections[-1][0] == "quic://example.test:4434"
    assert main(["me", "--cert-dir", str(lobby.directory), "--door", "quic://override:1"]) == 0
    assert lobby.connections[-1][0] == "quic://override:1"


def test_name_selects_normal_client_directory(lobby):
    argv = lobby.argv("--once")
    index = argv.index("--cert-dir")
    argv[index : index + 2] = ["--name", "mini"]
    lobby.grants = lambda d: [grant(d.fingerprint)]
    assert main(argv) == 0
    directory = door.default_cert_dir("mini")
    assert (directory / "client.key").exists()
    assert onboard.configured_door(directory) == "quic://example.test:4434"


def test_a_pin_with_colons_and_capitals_is_accepted(lobby):
    pin = ":".join(lobby.pin[i : i + 2] for i in range(0, 64, 2)).upper()
    assert main(lobby.argv("--pin", pin, "--once")) == 3
    assert len(lobby.posts) == 1


def test_a_different_certificates_entitlement_does_not_enroll_this_one(lobby):
    lobby.grants = [grant("00" * 32)]
    assert main(lobby.argv("--once")) == 3
    assert not (lobby.directory / "config.toml").exists()


def test_a_rerun_uses_the_saved_server_certificate(lobby):
    argv = lobby.argv("--once")
    assert main(argv) == 3
    index = argv.index("--server-cert")
    del argv[index : index + 2]
    lobby.server_path.unlink()
    assert main(argv) == 3
    assert len(lobby.posts) == 1


def test_a_different_server_certificate_requires_force_even_with_a_matching_pin(lobby):
    assert main(lobby.argv("--once")) == 3
    key = (lobby.directory / "client.key").read_bytes()
    other_pem, _, other_pin = certs.identity()
    lobby.server_path.write_text(other_pem)
    assert main(lobby.argv("--pin", other_pin, "--once")) == 2
    assert main(lobby.argv("--pin", other_pin, "--force", "--once")) == 3
    assert (lobby.directory / "client.key").read_bytes() == key
    assert len(lobby.posts) == 2


def test_corrupt_state_does_not_silently_repost(lobby):
    assert main(lobby.argv("--once")) == 3
    (lobby.directory / "onboard.json").write_text("broken")
    assert main(lobby.argv("--once")) == 2
    assert len(lobby.posts) == 1
