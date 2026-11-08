"""Discover readable feed layers and route questions using the caller's catalog."""

from __future__ import annotations

import json
import re

from . import door, files, message, show

_LAYER_IRI = re.compile(r"urn:layer:([a-z0-9-]+)\Z")


def definition(d, layer: str) -> dict:
    """Read the door's JSON definition, bound to the requested layer."""
    layer = door.layer_name(layer)
    doc = json.loads(d.source(f"urn:layer:{layer}", **{"as": "application/json"}).data)
    if not isinstance(doc, dict) or doc.get("layer") != layer:
        raise ValueError(f"the door did not return the definition for layer `{layer}`")
    return doc


def is_feed(doc: dict, layer: str) -> bool:
    """Whether this layer's JSON definition identifies it as a feed."""
    return doc.get("layer") == layer and doc.get("feed") is True and "Feed" in doc.get("types", [])


def require_feed(d, layer: str) -> None:
    """Refuse a non-feed layer before applying feed operations."""
    if not is_feed(definition(d, layer), layer):
        raise ValueError(f"layer `{layer}` is not a feed")


def catalog(d) -> dict[str, set[str]]:
    """Exact layer bindings in the door's capability-scoped catalog, grouped by name."""
    entries = d.entries()
    if entries is None:
        raise ValueError(
            "the door does not offer a catalog; cannot discover this identity's layers"
        )
    found: dict[str, set[str]] = {}
    for entry in entries:
        writable = entry.pattern.endswith(":say")
        base = entry.pattern.removesuffix(":say") if writable else entry.pattern
        match = _LAYER_IRI.fullmatch(base)
        if match:
            layer = door.layer_name(match.group(1))
            found.setdefault(layer, set()).add("say" if writable else "read")
    return found


def pages(d, layer: str, **filters):
    """All of a layer's entries, advancing only on valid increasing sequence numbers."""
    after, limit = 0, 25
    while True:
        doc = json.loads(
            d.source(
                f"urn:layer:{layer}:entries",
                after=str(after),
                limit=str(limit),
                **filters,
                **{"as": "application/ld+json"},
            ).data
        )
        page = doc.get("@graph", [])
        yield from page
        if len(page) < limit:
            break
        next_after = max(int(files.one(n.get("seq")) or 0) for n in page)
        if next_after <= after:
            raise ValueError(f"layer `{layer}` returned entries without advancing its sequence")
        after = next_after


def listing(d, args, say) -> int:
    """Print readable feeds and their newest entries, or one feed's concept tree."""
    if args.feed:
        require_feed(d, args.feed)
        if args.scheme:
            answer = d.source(f"urn:layer:{args.feed}:scheme", **{"as": "application/json"})
            if args.json:
                raise ValueError("--scheme prints a concept tree; omit --json")
            lines = Scheme(json.loads(answer.data)).lines()
            say(f"scheme for {args.feed}:")
            for line in lines or ["no concepts"]:
                say(line)
            return 0
        layers = [args.feed]
    else:
        if args.scheme:
            raise ValueError("name a feed: feeds FEED --scheme")
        layers = []
        for layer, verbs in sorted(catalog(d).items()):
            if "read" in verbs and is_feed(definition(d, layer), layer):
                layers.append(layer)
    result = []
    for layer in layers:
        newest = max(pages(d, layer), key=lambda n: int(files.one(n.get("seq")) or 0), default=None)
        result.append({"feed": layer, "newest": show.elided(newest) if newest else None})
        if not args.json:
            say(f"feed: {layer}")
            if newest is None:
                say("    no entries")
            else:
                for line in show.lines(newest):
                    say("    " + line)
    if args.json:
        say(json.dumps(result))
    elif not result:
        say("no readable feeds")
    return 0


def question_layer(d, selected: str | None) -> str:
    """The caller's sole writable non-feed channel; require selection if ambiguous."""
    candidates = []
    for layer, verbs in sorted(catalog(d).items()):
        if "say" not in verbs:
            continue
        graph = definition(d, layer)
        if not is_feed(graph, layer) and graph.get("model") == "channel":
            candidates.append(layer)
    if not candidates:
        raise ValueError(
            "no writable non-feed channel for this identity; joined public readers cannot ask "
            "until granted their own Talk channel"
        )
    if selected is not None:
        if selected not in candidates:
            raise ValueError(f"layer `{selected}` is not this identity's writable non-feed channel")
        return selected
    if len(candidates) != 1:
        raise ValueError(
            f"several writable channels: {', '.join(candidates)}; choose --layer LAYER"
        )
    return candidates[0]


def concepts(values: list[str]) -> str | None:
    """Validate repeatable concept IRIs and serialize the door's comma-separated filter."""
    for value in values:
        message.iri(value)
        if "," in value:
            raise ValueError("a concept IRI cannot contain a comma in this filter")
    return ",".join(dict.fromkeys(values)) if values else None


class Scheme:
    """A concept tree from the door's JSON face, with cycle and multi-parent handling."""

    def __init__(self, doc: dict):
        """Read labels and parent edges without interpreting RDF."""
        self.labels: dict[str, str] = {}
        self.children: dict[str, set[str]] = {}
        self.concepts: set[str] = set()
        for concept in doc["concepts"]:
            iri = concept["@id"]
            self.concepts.add(iri)
            self.labels[iri] = concept.get("prefLabel") or iri
            for parent in concept["broader"]:
                self.children.setdefault(parent, set()).add(iri)
                self.concepts.add(parent)

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
