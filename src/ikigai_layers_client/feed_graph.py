"""Read the named-node Turtle face used by layer definitions and SKOS schemes.

This is deliberately a small wire reader, not a general RDF engine. It accepts named
subjects, prefix declarations, predicate/object lists and string literals, including
language and datatype suffixes. Unsupported syntax is refused, never silently ignored.
"""

from __future__ import annotations

import ast
import re

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
SKOS = "http://www.w3.org/2004/02/skos/core#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"

_TOKEN = re.compile(
    r'\s+|\#[^\n]*|<[^<>\s]*>|"""(?:[^"\\]|\\.|"(?!""))*"""|'
    r'"(?:[^"\\]|\\.)*"|\^\^|[;,\.]|[^\s<>";,]+',
    re.DOTALL,
)


def triples(text: str) -> list[tuple[str, str, str]]:
    """Decode the door's Turtle triples; raise ValueError on unsupported syntax."""
    tokens, end = [], 0
    for match in _TOKEN.finditer(text):
        if match.start() != end:
            raise ValueError("unsupported Turtle in the door's graph")
        token, end = match.group(), match.end()
        if not token.isspace() and not token.startswith("#"):
            if not token.startswith(("<", '"')) and token != "." and token.endswith("."):
                tokens.extend((token[:-1], "."))
            else:
                tokens.append(token)
    if text[end:].strip():
        raise ValueError("unsupported Turtle in the door's graph")
    prefixes, result, pos = {}, [], 0

    def take():
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("incomplete Turtle in the door's graph")
        token = tokens[pos]
        pos += 1
        return token

    def named(token):
        if token.startswith("<") and token.endswith(">"):
            value = token[1:-1]
            if ":" in value and "\\" not in value:
                return value
        prefix, sep, local = token.partition(":")
        if sep and prefix in prefixes and not any(c in local for c in "[]()\\"):
            return prefixes[prefix] + local
        raise ValueError(f"unsupported Turtle name: {token!r}")

    while pos < len(tokens):
        first = take()
        if first.lower() in ("@prefix", "prefix"):
            prefix, target = take(), take()
            if not prefix.endswith(":"):
                raise ValueError("invalid Turtle prefix")
            prefixes[prefix[:-1]] = named(target)
            if first.startswith("@") and take() != ".":
                raise ValueError("invalid Turtle prefix terminator")
            continue
        subject = named(first)
        while True:
            predicate = take()
            predicate = RDF_TYPE if predicate == "a" else named(predicate)
            while True:
                obj = take()
                if obj.startswith('"'):
                    try:
                        # Turtle's string escapes are Python's common string escapes too.
                        value = ast.literal_eval(obj)
                    except (ValueError, SyntaxError) as exc:
                        raise ValueError("invalid Turtle literal") from exc
                    if pos < len(tokens) and tokens[pos].startswith("@"):
                        take()  # a language tag does not change a tree label
                    elif pos < len(tokens) and tokens[pos] == "^^":
                        take()
                        named(take())
                else:
                    value = named(obj)
                result.append((subject, predicate, value))
                punctuation = take()
                if punctuation != ",":
                    break
            if punctuation == ".":
                break
            if punctuation != ";":
                raise ValueError("unsupported Turtle predicate list")
            if pos < len(tokens) and tokens[pos] == ".":
                take()
                break
    return result


class Scheme:
    """A SKOS hierarchy, retaining IRIs and handling cycles and multiple parents."""

    def __init__(self, text: str):
        """Read concepts, labels, broader and narrower edges from Turtle."""
        self.labels: dict[str, str] = {}
        self.children: dict[str, set[str]] = {}
        self.concepts: set[str] = set()
        for subject, predicate, obj in triples(text):
            if predicate == RDF_TYPE and obj == SKOS + "Concept":
                self.concepts.add(subject)
            elif predicate in (SKOS + "prefLabel", RDFS + "label"):
                self.labels.setdefault(subject, obj)
            elif predicate in (SKOS + "broader", SKOS + "narrower"):
                parent, child = (obj, subject) if predicate.endswith("broader") else (subject, obj)
                self.children.setdefault(parent, set()).add(child)
                self.concepts.update((parent, child))

    def lines(self) -> list[str]:
        """An indented concept tree with labels, full IRIs, and named cycles."""
        children = {c for group in self.children.values() for c in group}
        roots = sorted(self.concepts - children)
        lines, visited = [], set()

        def visit(root):
            todo = [(root, 0, frozenset())]
            while todo:
                concept, depth, path = todo.pop()
                cycle, repeated = concept in path, concept in visited
                label = self.labels.get(concept, concept)
                suffix = " (cycle)" if cycle else " (also shown above)" if repeated else ""
                lines.append("    " * depth + f"{label} <{concept}>" + suffix)
                if cycle or repeated:
                    continue
                visited.add(concept)
                todo.extend(
                    (child, depth + 1, path | {concept})
                    for child in sorted(self.children.get(concept, ()), reverse=True)
                )

        for root in roots:
            visit(root)
        for concept in sorted(self.concepts - visited):
            if concept not in visited:
                visit(concept)
        return lines
