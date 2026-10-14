"""The follower's loop against a scripted door: lines, cursor, replays, outages, exits.

The behavior of the hub's ``layers-follow`` (its exit codes and its one-line-per-outage
rule), here over a fake connection so no door and no clock are needed.
"""

from __future__ import annotations

import json

import ikigai
import pytest
from ikigai import quic

from ikigai_layers_client.follow import Follower, format_cursor, parse_cursor, read_state


class Answer:
    def __init__(self, events, cursor=""):
        self.data = json.dumps({"cursor": cursor, "events": events}).encode()


def event(layer, seq, kind="say", by="urn:collab:participant:alice"):
    return {
        "layer": layer,
        "seq": seq,
        "time": f"2026-10-14T00:00:{seq:02d}.000Z",
        "kind": kind,
        "iri": f"urn:layer:{layer}:entry:{seq}",
        "by": by,
    }


class FakeDoor:
    """Answers each read with the next scripted item: a list of events or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.reads = []
        self.closed = False

    def source(self, iri, **args):
        assert iri == "urn:layers:feed" and args["as"] == "application/json"
        self.reads.append(args)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return Answer(item)

    def close(self):
        self.closed = True


def follower(tmp_path, script, *, layers=("talk",), connect_fails=0, **kwargs):
    door = FakeDoor(script)
    lines, sleeps, attempts = [], [], {"n": 0}

    def connect(timeout):
        attempts["n"] += 1
        if attempts["n"] <= connect_fails:
            raise ikigai.ConnectionLost("cannot reach quic://door:1 (refused)")
        return door

    f = Follower(
        connect,
        layers=list(layers),
        state=str(tmp_path / "cursor"),
        wait=kwargs.pop("wait", 50),
        say=lines.append,
        sleep=sleeps.append,
        backoff=5.0,
    )
    return f, door, lines, sleeps


def test_cursor_round_trip():
    assert parse_cursor("tasks:40,talk:7") == {"tasks": 40, "talk": 7}
    assert format_cursor({"tasks": 40, "talk": 7}) == "talk:7,tasks:40"
    for bad in ("talk", "Talk:1", "talk:x", "talk:1,talk:2"):
        with pytest.raises(ValueError):
            parse_cursor(bad)


def test_lines_are_the_text_face_and_the_cursor_follows_them(tmp_path):
    f, door, lines, _ = follower(tmp_path, [[event("talk", 1), event("talk", 2)]])
    assert f.run(once=True) == 0
    assert lines == [
        "talk 1 2026-10-14T00:00:01.000Z say urn:layer:talk:entry:1 urn:collab:participant:alice",
        "talk 2 2026-10-14T00:00:02.000Z say urn:layer:talk:entry:2 urn:collab:participant:alice",
    ]
    assert read_state(str(tmp_path / "cursor")) == {"talk": 2}
    assert door.reads[0]["after"] == "talk:0" and door.reads[0]["wait"] == "50"


def test_a_replayed_event_is_not_printed_twice(tmp_path):
    (tmp_path / "cursor").write_text("talk:2\n")
    f, _, lines, _ = follower(tmp_path, [[event("talk", 2), event("talk", 3)]])
    assert f.run(once=True) == 0
    assert [line.split()[1] for line in lines] == ["3"]


def test_from_now_skips_history_without_printing_it(tmp_path):
    f, door, lines, _ = follower(
        tmp_path, [[event("talk", 1), event("talk", 2)], [event("talk", 3)]]
    )
    assert f.run(from_now=True, max_reads=1) == 0
    assert [line.split()[1] for line in lines] == ["3"]
    assert door.reads[0]["wait"] == "1" and door.reads[1]["after"] == "talk:2"


def test_reset_starts_a_layer_again_from_zero(tmp_path):
    (tmp_path / "cursor").write_text("talk:9\n")
    f, door, _, _ = follower(tmp_path, [[]])
    assert f.run(reset=["talk"], once=True) == 0
    assert door.reads[0]["after"] == "talk:0"


def test_an_outage_is_reported_once_then_its_end(tmp_path):
    lost = ikigai.ConnectionLost("no answer within 80s")
    f, _, lines, sleeps = follower(tmp_path, [lost, lost, lost, [event("talk", 1)]])
    assert f.run(max_reads=1) == 0
    assert len([line for line in lines if "unreachable" in line]) == 1
    assert any("answers again" in line for line in lines)
    assert sleeps == [5.0, 10.0, 20.0]
    assert lines[-1].startswith("talk 1 ")


def test_a_door_that_never_connected_is_retried(tmp_path):
    f, _, lines, sleeps = follower(tmp_path, [[event("talk", 1)]], connect_fails=2)
    assert f.run(max_reads=1) == 0
    assert sleeps == [5.0, 10.0] and lines[-1].startswith("talk 1 ")


def test_once_and_unreachable_exits_5(tmp_path):
    f, _, lines, _ = follower(tmp_path, [], connect_fails=1)
    assert f.run(once=True) == 5
    assert "did not answer" in lines[-1]


def test_a_stale_cursor_stops_with_3(tmp_path):
    stale = ikigai.ConflictError("cursor talk:99 is past layer `talk`'s last event, 2")
    f, _, lines, _ = follower(tmp_path, [stale])
    assert f.run() == 3
    assert "--reset" in lines[-1]


@pytest.mark.parametrize(
    "refusal",
    [
        ikigai.DeniedError("no feed grant on layer `tasks`"),
        quic.NotEnrolledError("trusted but not enrolled", "ab" * 32),
        quic.ClientRefusedError("not in the trust list"),
        quic.PinMismatchError("not the pinned door"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_a_refusal_stops_with_4_and_names_it(tmp_path, refusal):
    f, _, lines, _ = follower(tmp_path, [refusal])
    assert f.run() == 4
    assert "STOPPED, the door refused" in lines[-1] and str(refusal.args[0]) in lines[-1]


def test_a_broken_cert_set_stops_with_2(tmp_path):
    f, _, lines, _ = follower(tmp_path, [quic.CertSetError("server.crt is missing")])
    assert f.run() == 2


def test_a_transient_door_error_backs_off(tmp_path):
    f, _, _, sleeps = follower(tmp_path, [ikigai.UnavailableError("host busy"), [event("talk", 1)]])
    assert f.run(max_reads=1) == 0 and sleeps == [5.0]


def test_an_unreadable_state_file_exits_2(tmp_path):
    (tmp_path / "cursor").write_text("garbage\n")
    f, _, lines, _ = follower(tmp_path, [])
    assert f.run() == 2 and "cannot read the cursor" in lines[-1]


def test_layers_not_followed_are_kept_in_the_state_file(tmp_path):
    (tmp_path / "cursor").write_text("other:4\n")
    f, _, _, _ = follower(tmp_path, [[event("talk", 1)]])
    assert f.run(once=True) == 0
    assert read_state(str(tmp_path / "cursor")) == {"other": 4, "talk": 1}
