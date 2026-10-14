"""What ``say`` writes: the ik:Message Turtle LAYERS.md shows, escaped correctly."""

from __future__ import annotations

import pytest

from ikigai_layers_client.message import iri, literal, message


def test_the_message_is_the_runbook_form():
    assert message("The layer door is up.") == (
        "@prefix ik: <https://ikigai-rs.dev/ns#> .\n"
        '<urn:layer:new> a ik:Message ; ik:body "The layer door is up." .\n'
    )


def test_a_reply_names_its_entry():
    turtle = message("yes", in_reply_to="urn:layer:talk:entry:01m4")
    assert "ik:inReplyTo <urn:layer:talk:entry:01m4>" in turtle


@pytest.mark.parametrize(
    ("text", "turtle"),
    [
        ('a "quote"', r'"a \"quote\""'),
        ("back\\slash", r'"back\\slash"'),
        ("two\nlines\r\tend", r'"two\nlines\r\tend"'),
        ("bell\x07", r'"bell\u0007"'),
        ("café ☃", '"café ☃"'),
    ],
)
def test_literals_escape_what_turtle_cannot_hold_raw(text, turtle):
    assert literal(text) == turtle


@pytest.mark.parametrize("bad", ["", "no-colon", "urn:x y", "urn:x>", 'urn:"x"'])
def test_an_iri_that_could_break_out_is_refused(bad):
    with pytest.raises(ValueError):
        iri(bad)
