"""Briefs, issues and files against a REAL Rust layer door, validating with the real shapes.

The `work` layer (fixtures/layers/layers/work.*) is a copy of the shapes and context of an edge
layer that takes briefs, issues and files, with only its name changed, so every write here is
admitted or refused by exactly the shapes a collaborator meets. Skips without ``ikigai`` on
PATH, and FAILS instead under ``--require-interop`` (CI's interop job).
"""

from __future__ import annotations

import json
import os

import ikigai
import pytest

from ikigai_layers_client import cli, files, message
from ikigai_layers_client import door as door_mod
from tests.layer_door import LayerDoor, require_ikigai
from tests.test_interop import run


@pytest.fixture(scope="module")
def door(request, tmp_path_factory):
    ikigai = require_ikigai(request)
    d = LayerDoor(ikigai, tmp_path_factory.mktemp("work-door"))
    yield d
    d.stop()


@pytest.fixture
def posted(door, capsys, tmp_path):
    """One of each kind, posted by alice; their IRIs by kind."""
    iris = {}
    brief = tmp_path / "brief.md"
    brief.write_text("# Add a door check\n\nRun it after every install.\n")
    code, out, err = run(
        capsys, "post", "brief", "work", str(brief), "--repo", "a/b", *door.args("alice")
    )
    assert code == 0, err
    iris["brief"] = out.strip()
    code, out, err = run(
        capsys,
        "post",
        "issue",
        "work",
        "--title",
        "Crash on start",
        "--severity",
        "major",
        *door.args("alice"),
        stdin="It crashes.\n",
    )
    assert code == 0, err
    iris["issue"] = out.strip()
    text = tmp_path / "notes.md"
    text.write_bytes("café notes\r\nline two\n".encode())
    code, out, err = run(
        capsys,
        "post",
        "file",
        "work",
        str(text),
        "--in-reply-to",
        iris["brief"],
        *door.args("alice"),
    )
    assert code == 0, err
    iris["text"] = out.strip()
    binary = tmp_path / "blob.bin"
    binary.write_bytes(bytes(range(256)) * 64)
    code, out, err = run(capsys, "post", "file", "work", str(binary), *door.args("alice"))
    assert code == 0, err
    iris["binary"] = out.strip()
    return iris, text, binary


def test_each_kind_is_admitted_and_read_back_as_itself(door, posted, capsys):
    iris, text, binary = posted
    assert all(i.startswith("urn:layer:work:entry:") for i in iris.values())
    code, out, _ = run(capsys, "read", "work", *door.args("alice-agent"))
    assert code == 0
    assert f"Brief {iris['brief']}" in out
    assert "    title: Add a door check" in out
    assert "    repository: https://github.com/a/b" in out
    assert "    Run it after every install." in out
    assert f"Issue {iris['issue']}" in out and "    severity: major" in out
    assert f"File {iris['text']}" in out and f"    in reply to {iris['brief']}" in out
    assert f"    file: notes.md ({len(text.read_bytes())} bytes, text/markdown, text)" in out
    assert "    file: blob.bin (16384 bytes, application/octet-stream, base64)" in out
    assert f"    digest: {files.digest(binary.read_bytes())}" in out
    # Never the content: not the text, not the base64.
    assert "line two" not in out and "AAECAwQF" not in out


def test_read_filters_by_kind_and_json_elides_content(door, posted, capsys):
    iris, _, _ = posted
    code, out, _ = run(capsys, "read", "work", "--kind", "issue", *door.args("alice"))
    assert code == 0 and iris["issue"] in out and iris["brief"] not in out
    code, out, _ = run(capsys, "read", "work", "--kind", "brief,file", *door.args("alice"))
    assert iris["brief"] in out and iris["binary"] in out and iris["issue"] not in out
    code, out, _ = run(capsys, "read", "work", "--json", "--kind", "file", *door.args("alice"))
    listed = json.loads(out)["@graph"]
    assert {n["@id"] for n in listed} >= {iris["text"], iris["binary"]}
    assert all("elided" in n["content"] for n in listed)


def test_save_writes_the_exact_bytes_and_will_not_overwrite(door, posted, capsys, tmp_path):
    iris, text, binary = posted
    out_dir = tmp_path / "saved"
    out_dir.mkdir()
    for iri, original in ((iris["text"], text), (iris["binary"], binary)):
        code, out, err = run(capsys, "save", iri, str(out_dir), *door.args("alice-agent"))
        assert code == 0, err
        saved = out_dir / original.name
        assert saved.read_bytes() == original.read_bytes()
        assert saved.stat().st_mode & 0o044, "readable as any new file is, not 0600"
        assert files.digest(original.read_bytes()) in out
    code, _, err = run(capsys, "save", iris["binary"], str(out_dir), *door.args("alice"))
    assert code == 2 and "exists" in err
    code, _, _ = run(capsys, "save", iris["binary"], str(out_dir), "--force", *door.args("alice"))
    assert code == 0
    code, _, err = run(capsys, "save", iris["brief"], str(out_dir), *door.args("alice"))
    assert code == 2 and "is a Brief, not a file" in err


def test_save_refuses_a_file_whose_content_does_not_match_its_digest(door, capsys, tmp_path):
    # The door checks the shape, never the digest: a well-formed entry can lie about its bytes.
    lie = (
        "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
        '<urn:layer:new> a ik:File ; ik:fileName "lie.txt" ; ik:mediaType "text/plain" ; '
        f'ik:contentHash "{files.digest(b"something else")}" ; ik:byteSize 5 ; '
        'ik:encoding "text" ; ik:content "hello" .\n'
    )
    with door_mod.connect(door.url, door.actors["alice"]) as d:
        iri = json.loads(d.sink("urn:layer:work:say", lie, **{"as": "application/json"}).data)[
            "@id"
        ]
    code, _, err = run(capsys, "save", iri, str(tmp_path), *door.args("alice"))
    assert code == 1 and "hashes to" in err and "not saved" in err
    assert os.listdir(tmp_path) == []  # nothing written, not even a temporary file


def test_a_file_over_the_layers_bound_is_refused_before_it_is_sent(door, capsys, tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(bytes(range(256)) * 1600)  # 409,600 bytes: 546,136 characters of base64
    code, _, err = run(capsys, "post", "file", "work", str(big), *door.args("alice"))
    assert code == 2
    assert "this layer takes at most 520192 bytes of content" in err
    assert "about 390144 bytes of binary" in err


def test_a_layer_without_file_shapes_is_refused_by_name(door, capsys, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi\n")
    code, _, err = run(capsys, "post", "file", "talk", str(f), *door.args("alice"))
    assert code == 2 and "does not take files" in err


@pytest.mark.parametrize(
    ("turtle", "refused"),
    [
        # A repository by name, not by web IRI.
        (
            'a ik:Brief ; dcterms:title "t" ; ik:body "b" ; ik:repository <urn:repo:x>',
            "ns#repository: PatternConstraintComponent",
        ),
        # A severity outside the closed list.
        (
            'a ik:Issue ; dcterms:title "t" ; ik:body "b" ; ik:severity <urn:iki:severity:praise>',
            "ns#severity: InConstraintComponent",
        ),
        # A file named with a path, and one named `..`.
        (
            'a ik:File ; ik:fileName "../x" ; ik:mediaType "text/plain" ; ik:contentHash '
            f'"{files.digest(b"x")}" ; ik:byteSize 1 ; ik:encoding "text" ; ik:content "x"',
            "ns#fileName: PatternConstraintComponent",
        ),
        (
            'a ik:File ; ik:fileName ".." ; ik:mediaType "text/plain" ; ik:contentHash '
            f'"{files.digest(b"x")}" ; ik:byteSize 1 ; ik:encoding "text" ; ik:content "x"',
            "ns#fileName: PatternConstraintComponent",
        ),
        # An encoding outside the closed flag.
        (
            'a ik:File ; ik:fileName "x" ; ik:mediaType "text/plain" ; ik:contentHash '
            f'"{files.digest(b"x")}" ; ik:byteSize 1 ; ik:encoding "hex" ; ik:content "78"',
            "ns#encoding: InConstraintComponent",
        ),
        # A brief with no title; a term the shapes do not name (closed).
        ('a ik:Brief ; ik:body "b"', "terms/title: MinCountConstraintComponent"),
        (
            'a ik:Brief ; dcterms:title "t" ; ik:body "b" ; ik:priority "high"',
            "ns#priority: ClosedConstraintComponent",
        ),
        # A reply to another layer's entry.
        (
            'a ik:Message ; ik:body "b" ; ik:inReplyTo <urn:layer:talk:entry:1>',
            "ns#inReplyTo: PatternConstraintComponent",
        ),
    ],
)
def test_the_shapes_refuse_what_they_should(door, turtle, refused):
    body = (
        "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
        "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
        f"<urn:layer:new> {turtle} .\n"
    )
    with door_mod.connect(door.url, door.actors["alice"]) as d:
        with pytest.raises(ikigai.EndpointError) as caught:
            d.sink("urn:layer:work:say", body, **{"as": "application/json"})
    assert "does not conform to layer `work`'s shapes" in caught.value.display()
    assert refused in caught.value.display()


def test_follow_describes_each_entry_and_filters_by_kind(door, posted, capsys, tmp_path):
    iris, _, _ = posted
    state = tmp_path / "c"
    code, out, _ = run(
        capsys, "follow", "--once", "--layers", "work", "--state", str(state), *door.args("alice")
    )
    assert code == 0
    by_iri = {line.split()[4]: line for line in out.splitlines()}
    assert by_iri[iris["brief"]].endswith('Brief "Add a door check" https://github.com/a/b')
    assert by_iri[iris["issue"]].endswith('Issue major "Crash on start"')
    assert " File blob.bin 16384 bytes application/octet-stream sha256:" in by_iri[iris["binary"]]
    code, out, _ = run(
        capsys,
        "follow",
        "--once",
        "--layers",
        "work",
        "--kind",
        "issue",
        "--state",
        str(tmp_path / "k"),
        *door.args("alice"),
    )
    assert code == 0 and out.strip() and all(" Issue " in line for line in out.splitlines())
    code, out, _ = run(
        capsys,
        "follow",
        "--once",
        "--plain",
        "--layers",
        "work",
        "--state",
        str(tmp_path / "p"),
        *door.args("alice"),
    )
    assert code == 0
    assert all(line.split()[-1].startswith("urn:collab:participant:") for line in out.splitlines())


def test_a_question_is_its_own_kind(door, capsys):
    code, out, _ = run(capsys, "say", "work", "Which door?", "--question", *door.args("alice"))
    assert code == 0
    iri = out.strip()
    code, out, _ = run(capsys, "read", "work", "--kind", "question", *door.args("alice"))
    assert f"Question {iri}" in out and "    Which door?" in out


def test_read_pages_through_every_entry(door, capsys):
    # More than one page (PAGE entries per read), so `read` must follow `after=` to the end.
    with door_mod.connect(door.url, door.actors["alice"]) as d:
        said = [
            json.loads(
                d.sink(
                    "urn:layer:work:say",
                    message.message(f"page test {i}"),
                    **{"as": "application/json"},
                ).data
            )["@id"]
            for i in range(cli.PAGE + 5)
        ]
    code, out, _ = run(capsys, "read", "work", "--kind", "message", *door.args("alice"))
    assert code == 0
    assert all(f"Message {iri}" in out for iri in said)
    headers = [line for line in out.splitlines() if not line.startswith("    ")]
    seqs = [int(line.split()[0]) for line in headers]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
