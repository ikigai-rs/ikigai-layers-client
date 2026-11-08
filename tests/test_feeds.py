"""Public feeds from a clean identity, filtering, and questions in one's own channel."""

import io
import json
from types import SimpleNamespace

import pytest

from ikigai_layers_client import certs, door, feeds, join
from ikigai_layers_client.cli import main
from ikigai_layers_client.follow import read_state
from tests.test_follow import FakeDoor, event

FEED = "releases"
ENTRY = "urn:layer:releases:entry:1"
PARENT = "urn:release:feature"
CHILD = "urn:release:feature:ui"
SCHEME = {
    "scheme": "urn:release:scheme",
    "label": "Releases",
    "concepts": [
        {"@id": PARENT, "prefLabel": "Features", "notation": "1", "broader": []},
        {"@id": CHILD, "prefLabel": "User interface", "notation": "2", "broader": [PARENT]},
    ],
}


@pytest.mark.parametrize("command", ["join", "feeds", "entries", "ask"])
def test_feed_commands_are_available(command, capsys):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def answer(doc):
    text = json.dumps(doc) if not isinstance(doc, str) else doc
    return SimpleNamespace(data=text.encode(), text=text)


def layer_definition(layer, *, feed=False, model="channel"):
    return {
        "layer": layer,
        "label": layer.title(),
        "model": model,
        "types": ["Layer", "Feed"] if feed else ["Layer"],
        "feed": feed,
        "public": feed,
    }


class FeedDoor(FakeDoor):
    """The existing scripted event door with feed definitions, entries and writes."""

    def __init__(self):
        super().__init__([[event(FEED, 1), event(FEED, 2), event(FEED, 3)]])
        self.definitions = {
            FEED: layer_definition(FEED, feed=True),
            "talk": layer_definition("talk"),
            "tasks": layer_definition("tasks", model="tuplespace"),
        }
        self.bindings = [f"urn:layer:{n}" for n in self.definitions] + ["urn:layer:talk:say"]
        self.nodes = [
            {
                "@id": f"urn:layer:releases:entry:{seq}",
                "@type": "Brief",
                "seq": str(seq),
                "title": f"Release {seq}",
                "subject": concept,
            }
            for seq, concept in ((1, CHILD), (2, "urn:release:fix"), (3, PARENT))
        ]
        self.calls, self.posts, self.connections = [], [], []

    def connect(self, url, directory, **kwargs):
        self.connections.append((url, directory))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def entries(self):
        return [SimpleNamespace(pattern=p) for p in self.bindings]

    def source(self, iri, **args):
        self.calls.append((iri, args))
        if iri == "urn:layers:feed":
            return super().source(iri, **args)
        if iri == f"urn:layer:{FEED}:scheme":
            assert args == {"as": "application/json"}
            return answer(SCHEME)
        if iri.startswith("urn:layer:") and iri.count(":") == 2:
            assert args == {"as": "application/json"}
            return answer(self.definitions[iri.split(":")[2]])
        if iri == f"urn:layer:{FEED}:entries":
            nodes = [n for n in self.nodes if int(n["seq"]) > int(args["after"])]
            if args.get("concept"):
                # The host, not the client, expands the current scheme's descendants.
                selected = set(args["concept"].split(","))
                if PARENT in selected:
                    selected.add(CHILD)
                nodes = [n for n in nodes if n["subject"] in selected]
            return answer({"@graph": nodes[: int(args["limit"])]})
        return answer({"@graph": [n for n in self.nodes if n["@id"] == iri]})

    def sink(self, iri, content, **args):
        self.posts.append((iri, content))
        return answer({"@id": "urn:layer:talk:entry:question"})


@pytest.fixture
def feed_door(monkeypatch):
    fake = FeedDoor()
    monkeypatch.setattr(door, "connect", fake.connect)
    return fake


def test_feeds_list_only_feed_definitions_and_the_newest_entry(feed_door, capsys):
    assert main(["feeds", "--name", "reader"]) == 0
    output = capsys.readouterr().out
    assert "feed: releases" in output and "Release 3" in output
    assert "Release 1" not in output and "feed: talk" not in output
    assert not feed_door.posts


def test_newest_entry_is_read_across_all_pages(feed_door, capsys):
    feed_door.nodes = [
        {
            "@id": f"urn:layer:releases:entry:{i}",
            "seq": str(i),
            "@type": "Brief",
            "title": f"Release {i}",
        }
        for i in range(1, 53)
    ]
    assert main(["feeds", "--name", "reader", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["newest"]["seq"] == "52"
    assert [a["after"] for iri, a in feed_door.calls if iri.endswith(":entries")] == [
        "0",
        "25",
        "50",
    ]


def test_scheme_prints_concept_labels_and_hierarchy(feed_door, capsys):
    assert main(["feeds", FEED, "--scheme", "--name", "reader"]) == 0
    output = capsys.readouterr().out
    assert f"Features <{PARENT}>" in output
    assert f"    User interface <{CHILD}>" in output


@pytest.mark.parametrize("command", ["entries", "read"])
def test_entries_send_repeatable_concepts_to_the_door_including_descendants(
    feed_door, capsys, command
):
    assert main([command, FEED, "--concept", PARENT, "--concept", CHILD, "--name", "reader"]) == 0
    output = capsys.readouterr().out
    assert "Release 1" in output and "Release 3" in output and "Release 2" not in output
    filters = [a["concept"] for iri, a in feed_door.calls if iri.endswith(":entries")]
    assert filters == [f"{PARENT},{CHILD}"]


@pytest.mark.parametrize("plain", [False, True])
def test_follow_filters_concepts_but_advances_over_unmatched_events(
    feed_door, capsys, tmp_path, plain
):
    state = tmp_path / "cursor"
    argv = [
        "follow",
        "--layers",
        FEED,
        "--once",
        "--state",
        str(state),
        "--concept",
        PARENT,
        "--name",
        "reader",
    ]
    assert main(argv + (["--plain"] if plain else [])) == 0
    output = capsys.readouterr().out
    assert "releases 1 " in output and "releases 3 " in output and "releases 2 " not in output
    assert read_state(str(state)) == {FEED: 3}
    assert ("Release 1" in output) is not plain


def test_ask_posts_subject_in_own_channel_and_never_in_the_feed(feed_door, capsys):
    assert main(["ask", ENTRY, 'Why "this" release?', "--name", "writer"]) == 0
    assert capsys.readouterr().out.strip() == "urn:layer:talk:entry:question"
    [(target, turtle)] = feed_door.posts
    assert target == "urn:layer:talk:say"
    assert "a ik:Question" in turtle and f"dcterms:subject <{ENTRY}>" in turtle
    assert r'ik:body "Why \"this\" release?"' in turtle


def test_ask_stdin_and_explicit_channel(feed_door, capsys, monkeypatch):
    feed_door.definitions["other"] = layer_definition("other")
    feed_door.bindings.append("urn:layer:other:say")
    assert main(["ask", ENTRY, "Why?", "--name", "writer"]) == 2
    assert "choose --layer" in capsys.readouterr().err
    assert not feed_door.posts
    monkeypatch.setattr("sys.stdin", io.StringIO("Why this?\n"))
    assert main(["ask", ENTRY, "-", "--layer", "other", "--name", "writer"]) == 0
    assert feed_door.posts[0][0] == "urn:layer:other:say"


@pytest.mark.parametrize("selection", [[], ["--layer", FEED], ["--layer", "talk"]])
def test_joined_reader_without_a_talk_channel_is_refused(feed_door, capsys, selection):
    feed_door.bindings = [f"urn:layer:{FEED}"]
    assert main(["ask", ENTRY, "Why?", "--name", "reader", *selection]) == 2
    assert "ikigai-layers: no writable non-feed channel" in capsys.readouterr().err
    assert not feed_door.posts


@pytest.mark.parametrize("target", [FEED, "tasks", "unknown"])
def test_ask_cannot_select_a_feed_tuplespace_or_ungranted_channel(feed_door, capsys, target):
    feed_door.bindings.extend([f"urn:layer:{FEED}:say", "urn:layer:tasks:say"])
    assert main(["ask", ENTRY, "Why?", "--layer", target, "--name", "writer"]) == 2
    assert "not this identity's writable non-feed channel" in capsys.readouterr().err
    assert not feed_door.posts


def test_nonfeed_concept_filter_is_refused(feed_door, capsys):
    assert main(["entries", "talk", "--concept", PARENT, "--name", "reader"]) == 2
    assert "not a feed" in capsys.readouterr().err


def test_scheme_cycles_terminate_and_are_named():
    doc = json.loads(json.dumps(SCHEME))
    doc["concepts"][0]["broader"] = [CHILD]
    scheme = feeds.Scheme(doc)
    assert any("(cycle)" in line for line in scheme.lines())


@pytest.fixture
def discovery(tmp_path, monkeypatch, feed_door):
    pem, _, pin = certs.identity()
    doc = {
        "door": "quic://example.test:4434",
        "join": "urn:layer:join:say",
        "server_cert": {"url": "server.crt", "sha256": pin},
        "public_layers": [
            {"name": FEED, "type": "ik:Feed", "read_only": True},
            {"name": "features", "type": "ik:Feed", "read_only": True},
            {"name": "talk", "type": "ik:Layer", "read_only": False},
        ],
    }
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("layers.json"):
            return json.dumps(doc).encode()
        assert url == "https://example.test/.well-known/ikigai/server.crt"
        return pem.encode()

    def sink(iri, content, **kwargs):
        feed_door.posts.append((iri, content))
        assert iri == doc["join"]
        assert kwargs == {"as": "application/json"}
        return answer(
            {
                "granted": [
                    f"urn:cap:layer:{verb}:{name}"
                    for name in (FEED, "features")
                    if f"<urn:layer:{name}>" in content
                    for verb in ("read", "feed")
                ]
            }
        )

    monkeypatch.setattr(join, "fetch", fetch)
    monkeypatch.setattr(feed_door, "sink", sink)
    directory = tmp_path / "reader"
    return SimpleNamespace(
        doc=doc,
        calls=calls,
        directory=directory,
        door=feed_door,
        argv=["join", "https://example.test", "--cert-dir", str(directory)],
    )


def test_join_creates_an_identity_pins_the_door_and_joins_all_public_feeds(discovery, capsys):
    assert main(discovery.argv) == 0
    output = capsys.readouterr().out
    assert "joined: " in output and "urn:cap:layer:read:releases" in output
    directory = discovery.directory
    for name in ("client.crt", "client.key", "server.crt", "config.toml"):
        assert (directory / name).is_file()
    assert (directory / "config.toml").read_text() == 'door = "quic://example.test:4434"\n'
    [(iri, turtle)] = discovery.door.posts
    assert iri == "urn:layer:join:say"
    assert "a onb:JoinRequest" in turtle and "onb:certificate" in turtle
    assert "<urn:layer:releases>, <urn:layer:features>" in turtle
    assert "<urn:layer:talk>" not in turtle
    assert "PRIVATE KEY" not in turtle and "PRIVATE KEY" not in output


def test_join_reuses_the_named_identity_and_supports_selected_feeds(discovery):
    argv = ["join", "https://example.test", FEED, "--cert-dir", str(discovery.directory)]
    assert main(argv) == 0
    key = (discovery.directory / "client.key").read_bytes()
    assert main(argv) == 0
    assert (discovery.directory / "client.key").read_bytes() == key
    assert "<urn:layer:features>" not in discovery.door.posts[0][1]


def test_join_sha256_mismatch_never_generates_an_identity_or_dials(discovery, capsys):
    discovery.doc["server_cert"]["sha256"] = "00" * 32
    assert main(discovery.argv) == 4
    assert "ikigai-layers: refused: server.crt does not match" in capsys.readouterr().err
    assert not discovery.directory.exists()
    assert not discovery.door.connections


@pytest.mark.parametrize("feed", ["talk", "private", "bad/../name"])
def test_join_refuses_anything_except_advertised_read_only_feeds(discovery, feed, capsys):
    argv = ["join", "https://example.test", feed, "--cert-dir", str(discovery.directory)]
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith("ikigai-layers: ")
    assert not discovery.directory.exists() and not discovery.door.connections


def test_join_keeps_a_differing_pin_or_saved_door(discovery, capsys):
    assert main(discovery.argv) == 0
    discovery.doc["door"] = "quic://other.test:4434"
    assert main(discovery.argv) == 2
    assert "use a separate --name" in capsys.readouterr().err
    assert len(discovery.door.posts) == 1


def test_join_refuses_an_incomplete_existing_identity(discovery, capsys):
    discovery.directory.mkdir()
    (discovery.directory / "client.key").write_text("keep")
    assert main(discovery.argv) == 2
    assert "needs both client.crt and client.key" in capsys.readouterr().err
    assert (discovery.directory / "client.key").read_text() == "keep"
    assert not discovery.door.connections


def test_join_does_not_accept_unexpected_write_grants(discovery, monkeypatch, capsys):
    monkeypatch.setattr(
        discovery.door, "sink", lambda *a, **kw: answer({"granted": [f"urn:cap:layer:say:{FEED}"]})
    )
    assert main(discovery.argv) == 2
    assert "only read/feed capabilities" in capsys.readouterr().err
    assert not (discovery.directory / "config.toml").exists()


@pytest.mark.parametrize(
    "url", ["http://example.test", "https://a:b@example.test", "https://example.test/path"]
)
def test_join_requires_an_https_host_without_credentials_or_path(url, monkeypatch, capsys):
    monkeypatch.setattr(join, "fetch", lambda url: pytest.fail("fetched invalid URL"))
    assert main(["join", url, "--name", "reader"]) == 2
    assert capsys.readouterr().err.startswith("ikigai-layers: ")


def test_join_defaults_to_the_public_host_and_named_set(discovery, monkeypatch, tmp_path):
    monkeypatch.setattr(door, "CERT_ROOT", tmp_path / "identities")
    previous = join.fetch

    def fetch_default(url):
        assert url.startswith("https://ikigai-rs.dev/")
        return previous(url.replace("https://ikigai-rs.dev/", "https://example.test/"))

    monkeypatch.setattr(join, "fetch", fetch_default)
    assert main(["join", FEED, "--name", "mini"]) == 0
    assert (tmp_path / "identities" / "mini" / "client.key").is_file()
    assert "<urn:layer:features>" not in discovery.door.posts[0][1]


def test_fetch_uses_a_bounded_https_request_and_timeout(monkeypatch):
    calls = []

    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.test/descriptor"

    class Opener:
        def open(self, url, timeout):
            calls.append((url, timeout))
            return Response(b'{"public_layers": []}')

    monkeypatch.setattr(join.urllib.request, "build_opener", lambda handler: Opener())
    assert join.fetch("https://example.test/descriptor") == b'{"public_layers": []}'
    assert calls == [("https://example.test/descriptor", 30)]
    monkeypatch.setattr(join, "MAX_DOCUMENT", 2)
    with pytest.raises(ValueError, match="exceeds 1 MiB"):
        join.fetch("https://example.test/descriptor")


def test_fetch_refuses_http_redirects_and_final_http_urls(monkeypatch):
    request = join.urllib.request.Request("https://example.test/descriptor")
    with pytest.raises(ValueError, match="must be HTTPS"):
        join._HTTPSRedirect().redirect_request(
            request, None, 302, "Found", {}, "http://example.test/descriptor"
        )

    class Response(io.BytesIO):
        def geturl(self):
            return "http://example.test/descriptor"

    opener = SimpleNamespace(open=lambda *a, **kw: Response(b"{}"))
    monkeypatch.setattr(join.urllib.request, "build_opener", lambda handler: opener)
    with pytest.raises(ValueError, match="must be HTTPS"):
        join.fetch("https://example.test/descriptor")


@pytest.mark.parametrize(
    "public", [None, ["releases"], [{"name": 123}], [{"name": FEED}, {"name": FEED}]]
)
def test_join_refuses_malformed_layer_lists_without_dialing(discovery, public, capsys):
    discovery.doc["public_layers"] = public
    assert main(discovery.argv) == 2
    assert capsys.readouterr().err.startswith("ikigai-layers: ")
    assert not discovery.door.connections


def test_empty_feeds_and_empty_entries_are_named(feed_door, capsys):
    feed_door.nodes = []
    assert main(["feeds", "--name", "reader"]) == 0
    assert "no entries" in capsys.readouterr().out
    feed_door.bindings = []
    assert main(["feeds", "--name", "reader"]) == 0
    assert capsys.readouterr().out.strip() == "no readable feeds"


def test_feed_json_elides_file_contents(feed_door, capsys):
    feed_door.nodes = [{"@id": ENTRY, "@type": "File", "seq": "1", "content": "secret file"}]
    assert main(["feeds", "--name", "reader", "--json"]) == 0
    output = capsys.readouterr().out
    assert "secret file" not in output
    assert "save" in json.loads(output)[0]["newest"]["content"]


@pytest.mark.parametrize("options", [["--scheme"], [FEED, "--scheme", "--json"]])
def test_bad_scheme_options_are_refused_before_dialing(monkeypatch, capsys, options):
    monkeypatch.setattr(door, "connect", lambda *a, **kw: pytest.fail("dialed the door"))
    assert main(["feeds", "--name", "reader", *options]) == 2
    assert capsys.readouterr().err.startswith("ikigai-layers: ")


def test_ask_refuses_a_nonfeed_entry_or_an_absent_entry(feed_door, capsys):
    assert main(["ask", "urn:layer:talk:entry:1", "Why?", "--name", "writer"]) == 2
    assert "not a feed" in capsys.readouterr().err
    assert main(["ask", "urn:layer:releases:entry:absent", "Why?", "--name", "writer"]) == 2
    assert "did not return feed entry" in capsys.readouterr().err
    assert not feed_door.posts


@pytest.mark.parametrize(
    "args",
    [
        ["ask", ENTRY, ""],
        ["ask", "bad", "Why?"],
        ["entries", FEED, "--concept", "bad"],
        ["follow", "--concept", "urn:a,b"],
    ],
)
def test_bad_feed_arguments_are_refused_before_dialing(monkeypatch, capsys, args):
    monkeypatch.setattr(door, "connect", lambda *a, **kw: pytest.fail("dialed the door"))
    assert main([*args, "--name", "reader"]) == 2
    assert capsys.readouterr().err.startswith("ikigai-layers: ")


def test_feed_entry_titles_are_shown_for_feed_specific_types(feed_door, capsys, tmp_path):
    for node in feed_door.nodes:
        node["@type"] = "FeedEntry"
    assert main(["feeds", "--name", "reader"]) == 0
    assert "title: Release 3" in capsys.readouterr().out
    assert (
        main(
            [
                "follow",
                "--layers",
                FEED,
                "--once",
                "--name",
                "reader",
                "--state",
                str(tmp_path / "cursor"),
            ]
        )
        == 0
    )
    assert 'FeedEntry "Release 1"' in capsys.readouterr().out


def test_definition_is_bound_to_the_requested_layer(feed_door, capsys):
    feed_door.definitions[FEED] = layer_definition("talk", feed=True)
    assert main(["feeds", FEED, "--scheme", "--name", "reader"]) == 2
    assert "definition for layer" in capsys.readouterr().err


def test_join_json_is_the_synchronous_answer(discovery, capsys):
    assert main([*discovery.argv, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert f"urn:cap:layer:read:{FEED}" in doc["granted"]


def test_follow_combines_kind_and_concepts_and_validates_the_layer(feed_door, tmp_path, capsys):
    argv = [
        "follow",
        "--layers",
        FEED,
        "--once",
        "--kind",
        "issue",
        "--concept",
        PARENT,
        "--concept",
        CHILD,
        "--state",
        str(tmp_path / "cursor"),
        "--name",
        "reader",
    ]
    assert main(argv) == 0
    assert capsys.readouterr().out == ""
    assert read_state(str(tmp_path / "cursor")) == {FEED: 3}
    assert {a["concept"] for iri, a in feed_door.calls if iri.endswith(":entries")} == {
        f"{PARENT},{CHILD}"
    }
    assert (
        main(
            [
                "follow",
                "--layers",
                "talk",
                "--once",
                "--concept",
                PARENT,
                "--state",
                str(tmp_path / "other"),
                "--name",
                "reader",
            ]
        )
        == 2
    )
    assert "not a feed" in capsys.readouterr().err


def test_feed_definitions_can_carry_boolean_and_integer_metadata(feed_door, capsys):
    feed_door.definitions[FEED].update(public=True, revision=1)
    assert main(["feeds", FEED, "--name", "reader"]) == 0
    assert "feed: releases" in capsys.readouterr().out


@pytest.mark.parametrize(
    "changes", [{"feed": False}, {"types": ["Layer"]}, {"label": "Feed", "types": ["Layer"]}]
)
def test_a_feed_needs_the_json_flag_and_compact_type(feed_door, capsys, changes):
    feed_door.definitions[FEED].update(changes)
    assert main(["feeds", FEED, "--scheme", "--name", "reader"]) == 2
    assert "not a feed" in capsys.readouterr().err


def test_scheme_keeps_multiple_parents_and_empty_schemes():
    doc = json.loads(json.dumps(SCHEME))
    doc["concepts"].append(
        {"@id": "urn:other", "prefLabel": "Other", "notation": "3", "broader": []}
    )
    doc["concepts"][1]["broader"].append("urn:other")
    lines = feeds.Scheme(doc).lines()
    assert sum(CHILD in line for line in lines) == 2
    assert any("(also shown above)" in line for line in lines)
    assert feeds.Scheme({"concepts": []}).lines() == []
