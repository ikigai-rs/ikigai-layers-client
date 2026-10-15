"""How ``read`` and ``follow`` show an entry: each kind by what a reader needs from it.

An entry arrives as one node of the layer's JSON-LD face (compacted with the layer's own
context, so ``title``, ``body``, ``fileName`` and the rest are its terms). A message or a
question shows its text; a brief its title and repository, then the brief; an issue its
severity and title, then the body; a file its name, size, media type and digest, and never its
content: a file is saved with ``ikigai-layers save ENTRY``, not printed.
"""

from __future__ import annotations

from .files import one
from .message import KINDS

#: How much of a message ``follow`` shows on its one line.
PREVIEW = 72


def kind(node: dict) -> str:
    """The entry's kind as the layer names its class (``Brief``), the first one this client
    knows when it has several, else every type it has, comma-separated."""
    types = node.get("@type", [])
    types = [types] if isinstance(types, str) else list(types)
    for known in KINDS.values():
        if known in types:
            return known
    return ",".join(types)


def _quoted(text, limit: int | None = None) -> str:
    text = " ".join(str(text).split())
    if limit is not None and len(text) > limit:
        text = text[: limit - 1] + "…"
    return '"' + text + '"'


def summary(node: dict) -> str:
    """One line about the entry, after its kind: what ``follow`` adds to the door's event line.

    >>> summary({"@type": "Issue", "title": "Crash on start", "severity": "urn:iki:severity:major"})
    'Issue major "Crash on start"'
    >>> summary({"@type": "File", "fileName": "a.png", "byteSize": "70", "mediaType": "image/png",
    ...          "contentHash": "sha256:" + "ab" * 32, "content": "iVBOR…"})
    'File a.png 70 bytes image/png sha256:abababababab…'
    """
    k = kind(node)
    if k == "Brief":
        repo = one(node.get("repository"))
        return f"Brief {_quoted(one(node.get('title')))}" + (f" {repo}" if repo else "")
    if k == "Issue":
        level = one(node.get("severity"))
        level = f" {str(level).rsplit(':', 1)[-1]}" if level else ""
        return f"Issue{level} {_quoted(one(node.get('title')))}"
    if k == "File":
        digest = str(one(node.get("contentHash")) or "")
        return (
            f"File {one(node.get('fileName'))} {one(node.get('byteSize'))} bytes "
            f"{one(node.get('mediaType'))} {digest[:19]}…"
        )
    body = one(node.get("body"))
    return f"{k} {_quoted(body, PREVIEW)}" if body is not None else k


def lines(node: dict) -> list[str]:
    """What ``read`` prints for the entry: a header line (seq, time, author, kind, IRI), then
    its details indented four spaces.

    >>> print("\\n".join(lines({"@id": "urn:layer:t:entry:1", "@type": "Brief", "seq": "3",
    ...     "created": "T", "attributedTo": "urn:collab:participant:a", "title": "Do X",
    ...     "body": "## Why\\nBecause."})))
    3 T urn:collab:participant:a Brief urn:layer:t:entry:1
        title: Do X
    <BLANKLINE>
        ## Why
        Because.
    """
    entry_id = node.get("@id", "?")
    k = kind(node)
    out = [
        f"{one(node.get('seq')) or '?'} {one(node.get('created')) or '?'} "
        f"{one(node.get('attributedTo')) or '?'} {k} {entry_id}"
    ]
    for term, words in (("inReplyTo", "in reply to"), ("supersedes", "supersedes")):
        target = one(node.get(term))
        if target:
            out.append(f"    {words} {target}")
    if k == "File":
        out.append(
            f"    file: {one(node.get('fileName'))} ({one(node.get('byteSize'))} bytes, "
            f"{one(node.get('mediaType'))}, {one(node.get('encoding'))})"
        )
        out.append(f"    digest: {one(node.get('contentHash'))}")
        out.append(f"    save it with: ikigai-layers save {entry_id}")
        return out
    if k in ("Brief", "Issue"):
        out.append(f"    title: {one(node.get('title'))}")
        level = one(node.get("severity"))
        if level:
            out.append(f"    severity: {str(level).rsplit(':', 1)[-1]}")
        repo = one(node.get("repository"))
        if repo:
            out.append(f"    repository: {repo}")
        out.append("")
    body = one(node.get("body"))
    if body is not None:
        out.extend(f"    {line}" for line in str(body).splitlines() or [""])
    return out


def elided(node: dict) -> dict:
    """The node with a file's content replaced by a note saying how to save it, so a JSON
    listing never carries base64 to a terminal (or into an agent's context)."""
    content = node.get("content")
    if content is None:
        return node
    size = len(str(one(content)))
    copy = dict(node)
    copy["content"] = f"<{size} characters elided: ikigai-layers save {node.get('@id')}>"
    return copy
