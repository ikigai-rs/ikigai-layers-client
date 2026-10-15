"""What ``say`` and ``post`` write: one entry about ``<urn:layer:new>``, as Turtle.

The form LAYERS.md's verification step writes, and the ikigai-layers README's "Writing an
entry"::

    @prefix ik: <https://ikigai-rs.dev/ns#> .
    <urn:layer:new> a ik:Message ; ik:body "The layer door is up." .

The host names the entry, and stamps who said it, when, and its place in the feed; a writer
that sends any of those is refused, so this sends only what the author says (and, for a reply
or a correction, the entry it answers or replaces).

Beside ``ik:Message`` (and ``ik:Question``, a message that asks), a layer whose shapes admit
them takes an ``ik:Brief`` (work, written to be done), an ``ik:Issue`` (a problem, written to be
fixed) and an ``ik:File`` (an attachment, :mod:`ikigai_layers_client.files`). What each may
carry is the layer's closed shapes; this module writes exactly those properties.
"""

from __future__ import annotations

import re

IK = "https://ikigai-rs.dev/ns#"
DCTERMS = "http://purl.org/dc/terms/"

#: The severities an issue may name, as ``urn:iki:severity:{name}`` (ikigai's review triage
#: list, less ``praise``, which is not an issue). Triage, never permission.
SEVERITIES = ("critical", "major", "minor", "info")

#: The entry kinds this client writes and shows, by the name a command takes.
KINDS = {
    "message": "Message",
    "question": "Question",
    "brief": "Brief",
    "issue": "Issue",
    "file": "File",
}

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

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


def repository(value: str) -> str:
    """A repository's web IRI: ``owner/name`` is a GitHub repository, and an ``https://`` IRI
    is taken as given. A bare name is refused: ``ikigai-core`` names a directory on one
    machine, and means nothing to a reader on another.

    >>> repository("ikigai-rs/ikigai-core")
    'https://github.com/ikigai-rs/ikigai-core'
    """
    if value.startswith("https://"):
        iri(value)
        return value
    if _REPO.match(value) and ".." not in value:
        return f"https://github.com/{value}"
    raise ValueError(
        f"`{value}` is not a repository: give owner/name (on GitHub) or its https:// IRI"
    )


def severity(name: str) -> str:
    """The IRI of one of :data:`SEVERITIES`, or ``ValueError`` naming them.

    >>> severity("major")
    'urn:iki:severity:major'
    """
    if name not in SEVERITIES:
        raise ValueError(f"`{name}` is not a severity ({', '.join(SEVERITIES)})")
    return f"urn:iki:severity:{name}"


def entry(
    kind: str,
    statements: list[str],
    *,
    in_reply_to: str | None = None,
    supersedes: str | None = None,
) -> str:
    """The Turtle for one entry of class ``ik:{kind}`` making ``statements`` (each
    ``predicate object``, already serialized), optionally answering or replacing an entry.

    >>> print(entry("Question", ['ik:body "Which door?"']), end="")
    @prefix ik: <https://ikigai-rs.dev/ns#> .
    <urn:layer:new> a ik:Question ; ik:body "Which door?" .
    """
    said = [f"a ik:{kind}", *statements]
    if in_reply_to is not None:
        said.append(f"ik:inReplyTo {iri(in_reply_to)}")
    if supersedes is not None:
        said.append(f"ik:supersedes {iri(supersedes)}")
    prefixes = f"@prefix ik: <{IK}> .\n"
    if any(s.startswith("dcterms:") for s in said):
        prefixes += f"@prefix dcterms: <{DCTERMS}> .\n"
    return f"{prefixes}<urn:layer:new> {' ; '.join(said)} .\n"


def message(
    body: str,
    *,
    in_reply_to: str | None = None,
    supersedes: str | None = None,
    question: bool = False,
) -> str:
    """The Turtle for one ``ik:Message`` saying ``body`` (an ``ik:Question`` with
    ``question``), optionally ``ik:inReplyTo`` or ``ik:supersedes`` an entry of the same layer.

    >>> print(message("The layer door is up."), end="")
    @prefix ik: <https://ikigai-rs.dev/ns#> .
    <urn:layer:new> a ik:Message ; ik:body "The layer door is up." .
    """
    return entry(
        "Question" if question else "Message",
        [f"ik:body {literal(body)}"],
        in_reply_to=in_reply_to,
        supersedes=supersedes,
    )


def brief(
    title: str,
    body: str,
    *,
    repo: str | None = None,
    in_reply_to: str | None = None,
    supersedes: str | None = None,
) -> str:
    """The Turtle for one ``ik:Brief``: a title, the brief as Markdown, and optionally the
    repository it is for (``owner/name`` or an ``https://`` IRI, :func:`repository`).

    >>> print(brief("Add a door check", "## Why", repo="ikigai-rs/ikigai-devtools"), end="")
    @prefix ik: <https://ikigai-rs.dev/ns#> .
    @prefix dcterms: <http://purl.org/dc/terms/> .
    <urn:layer:new> a ik:Brief ; dcterms:title "Add a door check" ; ik:body "## Why" ; \
ik:repository <https://github.com/ikigai-rs/ikigai-devtools> .
    """
    said = [f"dcterms:title {literal(title)}", f"ik:body {literal(body)}"]
    if repo is not None:
        said.append(f"ik:repository {iri(repository(repo))}")
    return entry("Brief", said, in_reply_to=in_reply_to, supersedes=supersedes)


def issue(
    title: str,
    body: str,
    *,
    repo: str | None = None,
    severity_name: str | None = None,
    in_reply_to: str | None = None,
    supersedes: str | None = None,
) -> str:
    """The Turtle for one ``ik:Issue``: a title and a body, and optionally its repository and
    one of :data:`SEVERITIES`.

    >>> "ik:severity <urn:iki:severity:minor>" in issue("Typo", "In the README.",
    ...                                                 severity_name="minor")
    True
    """
    said = [f"dcterms:title {literal(title)}", f"ik:body {literal(body)}"]
    if repo is not None:
        said.append(f"ik:repository {iri(repository(repo))}")
    if severity_name is not None:
        said.append(f"ik:severity {iri(severity(severity_name))}")
    return entry("Issue", said, in_reply_to=in_reply_to, supersedes=supersedes)
