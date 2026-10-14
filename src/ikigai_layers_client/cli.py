"""The ``ikigai-layers`` command: talk to a layer door with nothing but Python.

    ikigai-layers cert generate --dir DIR        one actor's client identity (no server key)
    ikigai-layers me                             who the door says this actor is
    ikigai-layers say LAYER [TEXT|-]             say one message (stdin with - or no TEXT)
    ikigai-layers read LAYER [--after N]         the entries after a sequence number
    ikigai-layers follow [--layers L,…]          one line per event, for a Monitor
    ikigai-layers tuples LAYER [--taken]         a tuplespace's pending tuples (or receipts)
    ikigai-layers out LAYER [FILE|-]             drop one tuple (Turtle or JSON-LD)
    ikigai-layers take LAYER SUBJECT             take one tuple, atomically

Every command but ``cert generate`` reaches the door (``--door``, default
``quic://ikigai-rs.dev:4434``) with one actor's cert set: ``--cert-dir DIR``, or ``--name
NAME`` for ``~/.ikigai/layers-client/NAME``. Exit codes: 0 done; 1 the door answered with an
error (named); 2 usage, or a problem on this side (named); 4 the door refused this
certificate, or no common wire version; 5 the door could not be reached. ``follow`` adds 3
(a stale cursor) and keeps its own meanings for 4 and 5 (see ``follow --help``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import ikigai
from ikigai import quic

from . import __version__, certs, door, follow, message

EXIT_DOOR_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 4
EXIT_UNREACHABLE = 5


class Usage(Exception):
    """A usage problem caught after parsing: exit 2 with its message."""


def _out(text: str = "") -> None:
    print(text, flush=True)


def _err(text: str) -> None:
    print(f"ikigai-layers: {text}", file=sys.stderr, flush=True)


# -- argument parsing -------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    connection = argparse.ArgumentParser(add_help=False)
    connection.add_argument(
        "--door", default=door.DOOR, help=f"the layer door (default {door.DOOR})"
    )
    who = connection.add_mutually_exclusive_group()
    who.add_argument("--name", help="the actor whose cert set to use: ~/.ikigai/layers-client/NAME")
    who.add_argument("--cert-dir", help="the cert set to use (server.crt, client.crt, client.key)")
    connection.add_argument(
        "--json", action="store_true", help="print the door's JSON answer as it came"
    )

    top = argparse.ArgumentParser(
        prog="ikigai-layers",
        description="Talk to an ikigai layer door over QUIC, with pinned certificates.",
        epilog="Exit codes: 0 done, 1 the door answered an error, 2 usage or local, "
        "4 refused, 5 unreachable.",
    )
    top.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = top.add_subparsers(dest="command", required=True, metavar="COMMAND")

    cert = commands.add_parser("cert", help="certificates")
    cert_commands = cert.add_subparsers(dest="cert_command", required=True, metavar="SUBCOMMAND")
    generate = cert_commands.add_parser(
        "generate",
        help="write one actor's client.crt and client.key (never a server key)",
        description="Write a self-signed ECDSA P-256 client certificate (SAN ikigai) and its "
        "key, mode 0600, in the layout `ikigai cert generate --dir` writes, and print the "
        "fingerprint the door's operator enrolls.",
    )
    where = generate.add_mutually_exclusive_group(required=True)
    where.add_argument("--dir", help="where to write client.crt and client.key")
    where.add_argument("--name", help="the actor: writes into ~/.ikigai/layers-client/NAME")
    generate.add_argument("--force", action="store_true", help="replace an existing pair")

    commands.add_parser("me", parents=[connection], help="who the door says this actor is")

    say = commands.add_parser("say", parents=[connection], help="say one message in a layer")
    say.add_argument("layer")
    say.add_argument("text", nargs="?", default="-", help="the message; - (default) reads stdin")
    say.add_argument("--in-reply-to", metavar="ENTRY", help="the entry this answers")

    read = commands.add_parser("read", parents=[connection], help="read a layer's entries")
    read.add_argument("layer")
    read.add_argument("--after", type=int, default=0, help="entries after this sequence number")

    fol = commands.add_parser(
        "follow",
        parents=[connection],
        help="one line per event, for Claude Code's Monitor",
        description=follow.__doc__.split("\n\n")[0],
        epilog=follow.__doc__.split("neither fixes itself:", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    fol.add_argument("--layers", default="talk", help="layers to follow, comma-separated")
    fol.add_argument(
        "--state",
        help="the cursor file (default ~/.ikigai/layers-client/<actor>.follow.cursor)",
    )
    fol.add_argument("--wait", type=int, help="seconds each read may wait (1..60; default 50)")
    fol.add_argument(
        "--from-now",
        action="store_true",
        help="for a layer with no cursor yet, start after its last event instead of at 0",
    )
    fol.add_argument(
        "--reset",
        action="append",
        default=[],
        metavar="LAYER",
        help="set LAYER's cursor back to 0 (after a restored door), then follow",
    )
    fol.add_argument("--once", action="store_true", help="one read, then exit")
    fol.add_argument("--max-reads", type=int, default=0, help="stop after N reads (0: never)")
    fol.add_argument(
        "--backoff", type=float, default=5.0, help="first retry delay (s), doubling to 300"
    )

    tuples = commands.add_parser("tuples", parents=[connection], help="a tuplespace's tuples")
    tuples.add_argument("layer")
    tuples.add_argument("--taken", action="store_true", help="the receipts of takes instead")

    out = commands.add_parser("out", parents=[connection], help="drop one tuple")
    out.add_argument("layer")
    out.add_argument("file", nargs="?", default="-", help="Turtle or JSON-LD; - (default) stdin")

    take = commands.add_parser("take", parents=[connection], help="take one tuple")
    take.add_argument("layer")
    take.add_argument("subject", help="the tuple's subject IRI")
    return top


def _cert_dir(args) -> Path:
    if args.cert_dir:
        return Path(args.cert_dir).expanduser()
    if args.name:
        return door.default_cert_dir(args.name)
    raise Usage("name the actor: --name NAME (or --cert-dir DIR)")


def _read_input(source: str, what: str) -> str:
    if source == "-":
        if sys.stdin.isatty():
            raise Usage(f"{what}: give it as an argument, or pipe it in")
        return sys.stdin.read()
    try:
        return Path(source).read_text()
    except OSError as e:
        raise Usage(f"cannot read {source}: {e}") from e


# -- the commands -------------------------------------------------------------


def cmd_cert_generate(args) -> int:
    """Write the pair and print what to do with it."""
    directory = Path(args.dir).expanduser() if args.dir else door.default_cert_dir(args.name)
    try:
        fingerprint = certs.generate(directory, force=args.force)
    except certs.CertExists as e:
        _err(str(e))
        return EXIT_USAGE
    _out(f"wrote client.crt and client.key to {directory} (no server key: none is needed)")
    _out(f"fingerprint: {fingerprint}")
    if not (directory / "server.crt").exists():
        _out(f"next: put the door's server.crt (its operator sends it) in {directory}")
    _out(
        "send the door's operator client.crt ONLY, never client.key, and read this "
        "fingerprint back to them over a different channel"
    )
    if (directory / "server.key").exists():
        _out(f"note: {directory / 'server.key'} is never used by a client; you may remove it")
    return 0


def cmd_me(d: door.Door, args) -> int:
    """``urn:layers:me`` as JSON."""
    answer = d.source("urn:layers:me", **{"as": "application/json"})
    if args.json:
        _out(answer.text)
        return 0
    me = json.loads(answer.data)
    _out(json.dumps(me, indent=2, sort_keys=True))
    return 0


def cmd_say(d: door.Door, args) -> int:
    """One ``ik:Message`` into ``urn:layer:{layer}:say``; prints the entry's IRI."""
    text = args.text
    if text == "-":
        # A piped body keeps the newline its producer ended it with; that newline is not
        # part of the message.
        text = _read_input("-", "the message").rstrip("\n")
    if not text:
        raise Usage("the message is empty")
    turtle = message.message(text, in_reply_to=args.in_reply_to)
    answer = d.sink(
        f"urn:layer:{door.layer_name(args.layer)}:say", turtle, **{"as": "application/json"}
    )
    _out(answer.text if args.json else json.loads(answer.data)["@id"])
    return 0


def _one(value):
    return value[0] if isinstance(value, list) and value else value


def cmd_read(d: door.Door, args) -> int:
    """The entries after ``--after``: a header line each, then its body indented."""
    layer = door.layer_name(args.layer)
    answer = d.source(
        f"urn:layer:{layer}:entries", after=str(args.after), **{"as": "application/ld+json"}
    )
    if args.json:
        _out(answer.text)
        return 0
    for entry in json.loads(answer.data).get("@graph", []):
        kind = entry.get("@type", "")
        kind = ",".join(kind) if isinstance(kind, list) else kind
        _out(
            f"{entry.get('seq', '?')} {entry.get('created', '?')} "
            f"{entry.get('attributedTo', '?')} {kind} {entry.get('@id', '?')}"
        )
        reply = _one(entry.get("inReplyTo"))
        if reply:
            _out(f"    in reply to {reply}")
        body = _one(entry.get("body"))
        if body is not None:
            for line in str(body).splitlines() or [""]:
                _out(f"    {line}")
    return 0


def cmd_tuples(d: door.Door, args) -> int:
    """The pending tuples (or, ``--taken``, the receipts), one line each."""
    layer = door.layer_name(args.layer)
    extra = {"state": "taken"} if args.taken else {}
    answer = d.source(f"urn:layer:{layer}:tuples", **extra, **{"as": "application/json"})
    if args.json:
        _out(answer.text)
        return 0
    listing = json.loads(answer.data)
    if args.taken:
        for r in listing.get("receipts", []):
            _out(f"{r['seq']} {r['time']} {r['subject']} {r['taker']} {r['receipt']}")
    else:
        for t in listing.get("tuples", []):
            _out(f"{t['seq']} {t['created']} {t['subject']} {t['dropper']}")
    return 0


def cmd_out(d: door.Door, args) -> int:
    """Drop one tuple; prints its subject."""
    content = _read_input(args.file, "the tuple")
    if not content.strip():
        raise Usage("the tuple is empty")
    layer = door.layer_name(args.layer)
    answer = d.sink(f"urn:layer:{layer}:tuples", content, **{"as": "application/json"})
    _out(answer.text if args.json else json.loads(answer.data)["@id"])
    return 0


def cmd_take(d: door.Door, args) -> int:
    """Take one tuple; prints it as taken: its nodes under the layer's context (``--json``:
    the whole JSON-LD document, context included)."""
    layer = door.layer_name(args.layer)
    answer = d.delete(
        f"urn:layer:{layer}:tuples", tuple=args.subject, **{"as": "application/ld+json"}
    )
    if args.json:
        _out(answer.text)
    else:
        _out(json.dumps(json.loads(answer.data).get("@graph", []), indent=2))
    return 0


def cmd_follow(args, cert_dir: Path) -> int:
    """Run the follower (``follow.Follower``) until it stops."""
    layers = [x.strip() for x in args.layers.split(",") if x.strip()]
    if not layers:
        raise Usage("no layers to follow")
    for layer in layers + args.reset:
        door.layer_name(layer)
    wait = args.wait if args.wait is not None else (1 if args.once else 50)
    if not 1 <= wait <= follow.MAX_WAIT:
        raise Usage(f"--wait must be 1..{follow.MAX_WAIT} (the door refuses a longer wait)")
    state = args.state or str(cert_dir.parent / f"{cert_dir.name}.follow.cursor")
    follower = follow.Follower(
        lambda timeout: door.connect(args.door, cert_dir, timeout=timeout),
        layers=layers,
        state=os.path.expanduser(state),
        wait=wait,
        say=_out,
        backoff=args.backoff,
    )
    try:
        return follower.run(
            from_now=args.from_now, reset=args.reset, once=args.once, max_reads=args.max_reads
        )
    finally:
        follower.close()


COMMANDS = {
    "me": cmd_me,
    "say": cmd_say,
    "read": cmd_read,
    "tuples": cmd_tuples,
    "out": cmd_out,
    "take": cmd_take,
}


def main(argv: list[str] | None = None) -> int:
    """Run one command; the exit code."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "cert":
            return cmd_cert_generate(args)
        cert_dir = _cert_dir(args)
        if getattr(args, "layer", None) is not None:
            door.layer_name(args.layer)  # a bad name is refused before anything is dialed
        quic.parse_door(args.door)
        if args.command == "follow":
            return cmd_follow(args, cert_dir)
        with door.connect(args.door, cert_dir) as d:
            return COMMANDS[args.command](d, args)
    except (Usage, ValueError) as e:
        _err(str(e))
        return EXIT_USAGE
    except quic.CertSetError as e:
        _err(str(e))
        return EXIT_USAGE
    except (quic.TrustError, ikigai.ProtocolError) as e:
        _err(f"refused: {e}")
        return EXIT_REFUSED
    except ikigai.ConnectionLost as e:
        _err(f"unreachable: {e}")
        return EXIT_UNREACHABLE
    except ikigai.EndpointError as e:
        _err(e.display())
        return EXIT_DOOR_ERROR
    except KeyboardInterrupt:
        return 130


def run() -> None:
    """The console-script entry point."""
    sys.exit(main())
