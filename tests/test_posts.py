"""Briefs, issues and files as entries, and how read and follow show them, without a door.

What each builder writes is checked against the shape it must pass (the real one is
fixtures/layers/layers/work.shapes.ttl, and tests/test_work.py posts to a real door that
validates against it); here the Turtle, the encoding choice, the digest check, the bound and
the display are pinned on their own.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from ikigai_layers_client import door, files, message, show
from ikigai_layers_client.cli import main

SHAPES = Path(__file__).parent / "fixtures" / "layers" / "layers" / "work.shapes.ttl"


def test_a_brief_carries_title_markdown_and_repository():
    turtle = message.brief("Do X", "# Do X\n\nBecause.", repo="ikigai-rs/ikigai-core")
    assert turtle.startswith(
        "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
        "@prefix dcterms: <http://purl.org/dc/terms/> .\n"
    )
    assert "a ik:Brief" in turtle
    assert 'dcterms:title "Do X"' in turtle
    assert r'ik:body "# Do X\n\nBecause."' in turtle
    assert "ik:repository <https://github.com/ikigai-rs/ikigai-core>" in turtle


def test_an_issue_names_its_severity_by_iri():
    turtle = message.issue("Crash", "On start.", severity_name="critical", in_reply_to="urn:x:1")
    assert "a ik:Issue" in turtle and "ik:severity <urn:iki:severity:critical>" in turtle
    assert "ik:inReplyTo <urn:x:1>" in turtle
    with pytest.raises(ValueError, match="not a severity"):
        message.issue("t", "b", severity_name="praise")


@pytest.mark.parametrize(
    ("given", "iri"),
    [
        ("ikigai-rs/ikigai-core", "https://github.com/ikigai-rs/ikigai-core"),
        ("https://git.example/a/b", "https://git.example/a/b"),
    ],
)
def test_a_repository_is_a_web_iri(given, iri):
    assert message.repository(given) == iri


@pytest.mark.parametrize("bad", ["ikigai-core", "a/b/c", "../x", "http://plain/a", "https://a b"])
def test_a_repository_that_is_not_one_is_refused(bad):
    with pytest.raises(ValueError):
        message.repository(bad)


def test_a_question_and_a_correction():
    turtle = message.message("Which?", question=True, supersedes="urn:layer:work:entry:1")
    assert "a ik:Question" in turtle and "ik:supersedes <urn:layer:work:entry:1>" in turtle


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        (b"plain\ntext\r\n\twith tabs\n", "text"),
        ("café ☃\n".encode(), "text"),
        (b"", "text"),
        (b"nul\x00inside", "base64"),
        (b"bell\x07", "base64"),
        (b"\xff\xfe not utf-8", "base64"),
        (bytes(range(256)), "base64"),
    ],
)
def test_text_when_it_can_be_base64_when_it_must(data, encoding):
    got, content = files.encode(data)
    assert got == encoding
    node = {
        "@id": "urn:layer:work:entry:1",
        "encoding": got,
        "content": content,
        "byteSize": str(len(data)),
        "contentHash": files.digest(data),
    }
    assert files.decode(node) == data  # the round trip is the bytes, exactly


def file_node(data: bytes, **override):
    encoding, content = files.encode(data)
    node = {
        "@id": "urn:layer:work:entry:1",
        "@type": "File",
        "fileName": "a.bin",
        "encoding": encoding,
        "content": content,
        "byteSize": str(len(data)),
        "contentHash": files.digest(data),
    }
    node.update(override)
    return node


@pytest.mark.parametrize(
    ("override", "says"),
    [
        ({"contentHash": "sha256:" + "0" * 64}, "hashes to"),
        ({"byteSize": "4"}, "it says 4"),
        ({"content": "!!not base64!!"}, "does not decode"),
        ({"contentHash": "md5:abc"}, "is not sha256"),
        ({"encoding": "hex"}, "no content"),
    ],
)
def test_a_file_that_does_not_match_itself_is_refused(override, says):
    with pytest.raises(files.IntegrityError, match=says):
        files.decode(file_node(b"\x00\x01\x02", **override))


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "x\ny", "\x00"])
def test_only_a_bare_name_is_saved(bad):
    with pytest.raises(ValueError):
        files.safe_name(bad)


def test_the_content_bound_comes_from_the_shapes():
    shapes = SHAPES.read_text()
    assert files.takes_files(shapes)
    assert files.content_bound(shapes) == 520192
    talk = (SHAPES.parent / "talk.shapes.ttl").read_text()
    assert not files.takes_files(talk) and files.content_bound(talk) is None


def test_a_file_over_the_bound_is_refused_naming_what_fits():
    data = bytes(range(256)) * 2048  # 512 KiB of binary: 699,052 characters of base64
    encoding, content = files.encode(data)
    why = files.too_large("big.bin", len(data), encoding, content, 520192)
    assert "big.bin is 524288 bytes" in why and "at most 520192 bytes" in why
    assert "about 390144 bytes of binary" in why
    small = b"x" * 1000
    assert files.too_large("s.txt", 1000, *files.encode(small), 520192) is None


def test_the_file_entry_names_everything_the_shape_requires():
    notes = b"# Notes\n"
    turtle, encoding, _ = files.file_entry("notes.md", notes)
    assert encoding == "text"
    for term in (
        'ik:fileName "notes.md"',
        'ik:mediaType "text/markdown"',
        f'ik:contentHash "{files.digest(notes)}"',
        "ik:byteSize 8",
        'ik:encoding "text"',
        r'ik:content "# Notes\n"',
    ):
        assert term in turtle
    _, encoding, content = files.file_entry("x.png", b"\x89PNG\r\n\x1a\n")
    assert encoding == "base64" and base64.b64decode(content) == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError, match="media type"):
        files.file_entry("x", b"x", media="Text/Plain; charset=utf-8")


def test_show_never_prints_a_files_content():
    node = file_node(b"\x00" * 300, mediaType="application/octet-stream")
    printed = "\n".join(show.lines(node)) + show.summary(node)
    assert node["content"] not in printed and "AAAA" not in printed
    assert "save it with: ikigai-layers save urn:layer:work:entry:1" in printed
    assert "elided" in show.elided(node)["content"]


def test_each_kind_shows_what_a_reader_needs():
    issue = {"@type": "Issue", "title": "Crash", "severity": "urn:iki:severity:major", "body": "x"}
    assert show.summary(issue) == 'Issue major "Crash"'
    assert "    severity: major" in show.lines(issue)
    brief = {"@type": "Brief", "title": "Do X", "repository": "https://github.com/a/b"}
    assert show.summary(brief) == 'Brief "Do X" https://github.com/a/b'
    long = {"@type": "Message", "body": "word " * 40}
    assert show.summary(long).endswith('…"') and len(show.summary(long)) < 90


@pytest.fixture
def no_dial(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("dialed the door")

    monkeypatch.setattr(door, "connect", refuse)


@pytest.mark.parametrize(
    "argv",
    [
        ["post", "file", "work", "/no/such/file", "--name", "a"],
        ["post", "brief", "work", "{body}", "--name", "a", "--repo", "ikigai-core"],
        ["post", "issue", "work", "{untitled}", "--name", "a"],
        ["post", "file", "work", "{body}", "--name", "a", "--file-name", "../x"],
        ["save", "urn:layer:work:item:1", "--name", "a"],
        ["read", "work", "--name", "a", "--kind", "memo"],
        ["follow", "--name", "a", "--kind", "brief,,memo"],
    ],
)
def test_usage_is_refused_before_dialing(no_dial, argv, capsys, tmp_path):
    body = tmp_path / "body.md"
    body.write_text("# A title\n\nThe body.\n")
    untitled = tmp_path / "untitled.md"
    untitled.write_text("No heading here.\n")
    argv = [a.format(body=body, untitled=untitled) for a in argv]
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith("ikigai-layers: ")
