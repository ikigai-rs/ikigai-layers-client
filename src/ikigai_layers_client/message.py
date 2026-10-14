"""What ``say`` writes: one ``ik:Message`` about ``<urn:layer:new>``, as Turtle.

The form LAYERS.md's verification step writes, and the ikigai-layers README's "Writing an
entry"::

    @prefix ik: <https://ikigai-rs.dev/ns#> .
    <urn:layer:new> a ik:Message ; ik:body "The layer door is up." .

The host names the entry, and stamps who said it, when, and its place in the feed; a writer
that sends any of those is refused, so this sends only the body (and, for a reply, the entry
it answers).
"""

from __future__ import annotations

IK = "https://ikigai-rs.dev/ns#"

# Characters an IRIREF may not hold (Turtle 1.1, production [18]).
_NOT_IN_IRI = set('<>"{}|^`\\') | {chr(c) for c in range(0x21)}


def literal(text: str) -> str:
    """``text`` as a Turtle double-quoted string literal, every character it may not hold
    raw escaped.

    >>> literal('say "hi"\\n')
    '"say \\\\"hi\\\\"\\\\n"'
    """
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def iri(value: str) -> str:
    """``value`` as a Turtle IRI reference ``<…>``, or ``ValueError`` when it cannot be one."""
    if not value or any(ch in _NOT_IN_IRI for ch in value) or ":" not in value:
        raise ValueError(f"{value!r} is not an absolute IRI")
    return f"<{value}>"


def message(body: str, *, in_reply_to: str | None = None) -> str:
    """The Turtle for one ``ik:Message`` saying ``body`` (optionally ``ik:inReplyTo`` an
    entry of the same layer).

    >>> print(message("The layer door is up."), end="")
    @prefix ik: <https://ikigai-rs.dev/ns#> .
    <urn:layer:new> a ik:Message ; ik:body "The layer door is up." .
    """
    statements = ["a ik:Message", f"ik:body {literal(body)}"]
    if in_reply_to is not None:
        statements.append(f"ik:inReplyTo {iri(in_reply_to)}")
    return f"@prefix ik: <{IK}> .\n<urn:layer:new> {' ; '.join(statements)} .\n"
