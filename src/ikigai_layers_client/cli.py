"""The ``ikigai-layers`` command: talk to a layer door with nothing but Python.

    ikigai-layers cert generate --dir DIR        one actor's client identity (no server key)
    ikigai-layers me                             who the door says this actor is
    ikigai-layers say LAYER [TEXT|-]             say one message (stdin with - or no TEXT)
    ikigai-layers post brief LAYER [FILE|-]      post a brief (Markdown; --title, --repo)
    ikigai-layers post issue LAYER [FILE|-]      post an issue (--title, --repo, --severity)
    ikigai-layers post file LAYER PATH           attach a file (text or base64, digest checked)
    ikigai-layers save ENTRY [PATH]              write a file entry to disk (default ~/Downloads)
    ikigai-layers read LAYER [--after N]         the entries after a sequence number (--kind)
    ikigai-layers follow [--layers L,…]          one line per event, for a Monitor (--kind)
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
import re
import sys
import tempfile
from pathlib import Path

import ikigai
from ikigai import quic

from . import __version__, certs, door, files, follow, message, show

EXIT_DOOR_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 4
EXIT_UNREACHABLE = 5

#: Entries per ``entries`` read. Small, because a layer that takes files may hold entries of
#: hundreds of KiB each, and every answer crosses the wire as one frame; ``read`` pages.
PAGE = 25

_ENTRY = re.compile(r"^urn:layer:([a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?):entry:[^\s<>\"]+$")
_HEADING = re.compile(r"^#\s+(.+?)\s*#*\s*$", re.M)


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

    thread_args = argparse.ArgumentParser(add_help=False)
    thread_args.add_argument("--in-reply-to", metavar="ENTRY", help="the entry this answers")
    thread_args.add_argument("--supersedes", metavar="ENTRY", help="your entry this replaces")

    say = commands.add_parser(
        "say", parents=[connection, thread_args], help="say one message in a layer"
    )
    say.add_argument("layer")
    say.add_argument("text", nargs="?", default="-", help="the message; - (default) reads stdin")
    say.add_argument("--question", action="store_true", help="ask it: an ik:Question")

    post = commands.add_parser("post", help="post a brief, an issue or a file")
    kinds = post.add_subparsers(dest="post_kind", required=True, metavar="KIND")
    for name, what in (("brief", "work, written to be done"), ("issue", "a problem to fix")):
        p = kinds.add_parser(
            name,
            parents=[connection, thread_args],
            help=f"post a {name}: {what}",
            description=f"Post an ik:{name.title()}: a title and a Markdown body (from FILE, "
            "or stdin with - or no FILE). The title defaults to the body's first `# ` heading.",
        )
        p.add_argument("layer")
        p.add_argument("source", nargs="?", default="-", help="the body; - (default) reads stdin")
        p.add_argument("--title", help="the title (default: the body's first `# ` heading)")
        p.add_argument("--repo", help="the repository: owner/name on GitHub, or an https:// IRI")
        if name == "issue":
            p.add_argument("--severity", choices=message.SEVERITIES, help="how urgent (triage)")
    attach = kinds.add_parser(
        "file",
        parents=[connection, thread_args],
        help="attach a file",
        description="Post an ik:File holding PATH whole: its name, media type, SHA-256 and "
        "size, and its bytes as text (UTF-8 with no control characters but tab, newline and "
        "carriage return) or else base64. A file over the layer's bound is refused here.",
    )
    attach.add_argument("layer")
    attach.add_argument("path", help="the file to attach")
    attach.add_argument("--file-name", help="the name to post it under (default: its own)")
    attach.add_argument("--media-type", help="its media type (default: guessed from its name)")

    save = commands.add_parser(
        "save",
        parents=[connection],
        help="write a file entry back to disk",
        description="Write a file entry's bytes to PATH (a directory: the file's own name in "
        "it; no PATH: your downloads directory, ~/Downloads), after checking its size and "
        "SHA-256. Nothing is written when either does not match. A name an agent or a shell "
        "loads as configuration (CLAUDE.md, AGENTS.md, .mcp.json, .envrc, any .git name), any "
        "name beginning with a dot, any ':', and a Windows device name (CON, NUL, COM1) are "
        "refused: save it under another name with --as NAME.",
    )
    save.add_argument("entry", help="the file entry's IRI (urn:layer:LAYER:entry:ID)")
    save.add_argument(
        "path",
        nargs="?",
        help="a directory to save into, or the file to write (default: your downloads directory)",
    )
    save.add_argument(
        "--as",
        dest="as_name",
        metavar="NAME",
        help="save under NAME instead of the entry's own file name (the same rules apply)",
    )
    save.add_argument(
        "--force",
        action="store_true",
        help="replace an existing file at the target; its old content is lost",
    )

    kind_help = f"only these kinds, comma-separated ({', '.join(message.KINDS)})"
    read = commands.add_parser("read", parents=[connection], help="read a layer's entries")
    read.add_argument("layer")
    read.add_argument("--after", type=int, default=0, help="entries after this sequence number")
    read.add_argument("--kind", help=kind_help)

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
    fol.add_argument("--kind", help=kind_help + "; other events are skipped")
    fol.add_argument(
        "--plain",
        action="store_true",
        help="the door's event line only (no kind or title after it, no entry read per event)",
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
    turtle = message.message(
        text, in_reply_to=args.in_reply_to, supersedes=args.supersedes, question=args.question
    )
    return _say(d, args, turtle)


def _say(d: door.Door, args, turtle: str) -> int:
    """Write one entry to ``urn:layer:{layer}:say``; prints the IRI the door named it."""
    answer = d.sink(
        f"urn:layer:{door.layer_name(args.layer)}:say", turtle, **{"as": "application/json"}
    )
    _out(answer.text if args.json else json.loads(answer.data)["@id"])
    return 0


def _titled_body(args, what: str) -> tuple[str, str]:
    """The body (FILE, or stdin) and the title: ``--title``, else the body's first heading."""
    body = _read_input(args.source, f"the {what}")
    if args.source == "-":
        body = body.rstrip("\n")
    if not body.strip():
        raise Usage(f"the {what} is empty")
    title = args.title
    if title is None:
        heading = _HEADING.search(body)
        if heading is None:
            raise Usage(f"give the {what} a --title, or start it with a `# Title` line")
        title = heading.group(1)
    if not title.strip():
        raise Usage("the title is empty")
    return title.strip(), body


def cmd_post(d: door.Door, args) -> int:
    """Post a brief, an issue or a file; prints the entry's IRI."""
    if args.post_kind == "file":
        return _post_file(d, args)
    title, body = args.prepared
    if args.post_kind == "brief":
        turtle = message.brief(
            title, body, repo=args.repo, in_reply_to=args.in_reply_to, supersedes=args.supersedes
        )
    else:
        turtle = message.issue(
            title,
            body,
            repo=args.repo,
            severity_name=args.severity,
            in_reply_to=args.in_reply_to,
            supersedes=args.supersedes,
        )
    return _say(d, args, turtle)


def _file_post(args) -> tuple[str, int, str, str, str]:
    """Read the file and build its entry: ``(name, size, turtle, encoding, content)``."""
    path = Path(args.path).expanduser()
    try:
        data = path.read_bytes()
    except OSError as e:
        raise Usage(f"cannot read {path}: {e}") from e
    name = args.file_name or path.name
    try:
        turtle, encoding, content = files.file_entry(
            name,
            data,
            media=args.media_type,
            in_reply_to=args.in_reply_to,
            supersedes=args.supersedes,
        )
    except files.UnsafeName as e:
        raise Usage(
            f"{e}, and a reader cannot save it: post it under another name with --file-name NAME"
        ) from e
    return name, len(data), turtle, encoding, content


def _post_file(d: door.Door, args) -> int:
    name, size, turtle, encoding, content = args.prepared
    layer = door.layer_name(args.layer)
    shapes = d.source(f"urn:layer:{layer}:shapes").text
    if not files.takes_files(shapes):
        raise Usage(f"layer `{layer}` does not take files: its shapes admit no ik:File")
    bound = files.content_bound(shapes)
    if bound is not None:
        why = files.too_large(name, size, encoding, content, bound)
        if why:
            raise Usage(why)
    return _say(d, args, turtle)


def _entry_layer(entry: str) -> str:
    match = _ENTRY.match(entry)
    if match is None:
        raise Usage(f"`{entry}` is not an entry IRI (urn:layer:LAYER:entry:ID)")
    return match.group(1)


_ANOTHER_NAME = "save it under another name with --as NAME"


def _save_dir(args) -> Path | None:
    """The directory ``save`` writes into when PATH names one or is left out; ``None`` when
    PATH names the file itself. Checked before dialing, with ``--as`` and PATH's own name."""
    if args.path is None:
        found = files.downloads_dir()
        if found is None:
            raise Usage(
                f"no downloads directory found (no {Path.home() / 'Downloads'}): give a PATH "
                "to save into"
            )
        return found
    path = Path(args.path).expanduser()
    if path.is_dir():
        return path
    if args.as_name is not None:
        raise Usage(
            f"{path} is not a directory: give --as NAME with a directory, or a PATH that "
            "names the file"
        )
    try:
        files.safe_name(path.name)
    except files.UnsafeName as e:
        raise Usage(f"not saved: {e}; give PATH another name") from e
    return None


def _target(name: str, path: str | None, directory: Path | None) -> Path:
    return directory / name if directory is not None else Path(path).expanduser()


def cmd_save(d: door.Door, args) -> int:
    """Write a file entry's bytes to disk, after checking its size and digest."""
    answer = d.source(args.entry, **{"as": "application/ld+json"})
    nodes = [n for n in json.loads(answer.data).get("@graph", []) if n.get("@id") == args.entry]
    if not nodes:
        _err(f"not saved: the door's answer for {args.entry} holds no such entry")
        return EXIT_DOOR_ERROR
    node = nodes[0]
    if show.kind(node) != "File":
        raise Usage(f"{args.entry} is a {show.kind(node) or 'entry'}, not a file")
    # The entry's own name matters only when it picks the file: not with --as, and not when
    # PATH names the file (both were checked before dialing).
    name = args.as_name if args.save_dir is not None else Path(args.path).expanduser().name
    try:
        if name is None:
            name = files.safe_name(str(files.one(node.get("fileName"))))
        data = files.decode(node)
    except files.UnsafeName as e:
        _err(f"not saved: {e}; {_ANOTHER_NAME}")
        return EXIT_DOOR_ERROR
    except (ValueError, files.IntegrityError) as e:
        _err(f"not saved: {e}")
        return EXIT_DOOR_ERROR
    target = _target(name, args.path, args.save_dir)
    if target.exists() and not args.force:
        raise Usage(
            f"{target} exists: give another PATH, --as NAME, or --force to replace it "
            "(its old content is lost)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ikigai-layers-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # mkstemp makes the file 0600; a saved file gets the mode any new file would.
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(tmp, 0o666 & ~mask)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _out(f"saved {target} ({len(data)} bytes, {files.digest(data)})")
    return 0


def _kinds(value: str | None) -> set[str] | None:
    """``--kind brief,issue`` as the classes ``{"Brief", "Issue"}``; ``None`` for every kind."""
    if value is None:
        return None
    named = [k.strip().lower() for k in value.split(",") if k.strip()]
    unknown = [k for k in named if k not in message.KINDS]
    if not named or unknown:
        raise Usage(
            f"--kind takes {', '.join(message.KINDS)}"
            + (f" (not {', '.join(unknown)})" if unknown else "")
        )
    return {message.KINDS[k] for k in named}


def cmd_read(d: door.Door, args) -> int:
    """The entries after ``--after``, page by page: each a header line, then what its kind
    shows indented (:mod:`show`); ``--json`` lists them with any file content elided."""
    layer = door.layer_name(args.layer)
    kinds = _kinds(args.kind)
    # One kind is filtered at the door (`type=`, an exact class); several here.
    by_type = {"type": message.IK + next(iter(kinds))} if kinds and len(kinds) == 1 else {}
    after, listed, context = args.after, [], None
    while True:
        answer = d.source(
            f"urn:layer:{layer}:entries",
            after=str(after),
            limit=str(PAGE),
            **by_type,
            **{"as": "application/ld+json"},
        )
        doc = json.loads(answer.data)
        context = doc.get("@context", context)
        page = sorted(doc.get("@graph", []), key=lambda n: int(files.one(n.get("seq")) or 0))
        for node in page:
            if kinds is not None and show.kind(node) not in kinds:
                continue
            if args.json:
                listed.append(show.elided(node))
            else:
                for line in show.lines(node):
                    _out(line)
        if len(page) < PAGE:
            break
        after = max(int(files.one(n.get("seq"))) for n in page)
    if args.json:
        _out(json.dumps({"@context": context or {}, "@graph": listed}))
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
        describe=not args.plain,
        kinds=_kinds(args.kind),
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
    "post": cmd_post,
    "save": cmd_save,
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
        # A bad name, entry, file or option is refused before anything is dialed.
        if getattr(args, "layer", None) is not None:
            door.layer_name(args.layer)
        if args.command == "save":
            _entry_layer(args.entry)
            if args.as_name is not None:
                try:
                    files.safe_name(args.as_name)
                except files.UnsafeName as e:
                    raise Usage(f"not saved: {e}; choose another --as NAME") from e
            args.save_dir = _save_dir(args)
        if args.command == "post" and args.post_kind == "file":
            args.prepared = _file_post(args)
        elif args.command == "post":
            if args.repo is not None:
                message.repository(args.repo)
            args.prepared = _titled_body(args, args.post_kind)
        if args.command in ("read", "follow"):
            _kinds(args.kind)
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
