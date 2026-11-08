"""Discover readable feed layers and route questions using the caller's catalog."""

from __future__ import annotations

import json
import re

from . import door, feed_graph, files, message, show

_LAYER_IRI = re.compile(r"urn:layer:([a-z0-9-]+)\Z")


def definition(d, layer: str) -> list[tuple[str, str, str]]:
    """Read a layer's definition in the door's Turtle face."""
    return feed_graph.triples(d.source(f"urn:layer:{door.layer_name(layer)}").text)


def is_feed(graph, layer: str) -> bool:
    """Whether the definition types this layer, rather than another subject, as ik:Feed."""
    return (f"urn:layer:{layer}", feed_graph.RDF_TYPE, message.IK + "Feed") in graph


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
            answer = d.source(f"urn:layer:{args.feed}:scheme")
            if args.json:
                raise ValueError("--scheme prints a concept tree; omit --json")
            lines = feed_graph.Scheme(answer.text).lines()
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
        if (
            not is_feed(graph, layer)
            and (f"urn:layer:{layer}", message.IK + "layerModel", "channel") in graph
        ):
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
