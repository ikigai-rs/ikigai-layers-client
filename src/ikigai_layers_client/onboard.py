"""Enroll a locally generated identity through a pinned door's lobby.

The request bytes and proof match ikigai-gonk-starter's worker_onboard.py. Grants use
ikigai-layers' onboard/layers/grants.context.jsonld and grants.shapes.ttl contract:
capability IRIs in onb:layerGrant, and ik:supersedes for replacement and revocation.
No subprocesses, key copies, or symlinks are needed, including on Windows.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import socket
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import ikigai
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from ikigai import quic

from . import certs, door

ONB = "https://ikigai-rs.dev/ns/onboard#"
IK = "https://ikigai-rs.dev/ns#"
PAGE = 100
_CAP = re.compile(r"urn:cap:layer:(read|feed|say|out|take):([a-z0-9-]{1,24})\Z")


def proof(code: str, fingerprint: str) -> str:
    """HMAC-SHA256(code's UTF-8, fingerprint's ASCII), in lowercase hex."""
    return hmac.new(code.encode("utf-8"), fingerprint.encode("ascii"), hashlib.sha256).hexdigest()


def request_turtle(invite: str, certificate_pem: str, fingerprint: str, proof_hex: str) -> str:
    """The starter's exact skolemized request bytes, including its final newline."""
    pem = certificate_pem.strip() + "\n"
    literal = (
        '"'
        + (
            pem.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        + '"'
    )
    return (
        f"@prefix onb: <{ONB}> .\n"
        f"<urn:iki:onboard:request:{fingerprint}> a onb:EnrollmentRequest ;\n"
        f"    onb:invite <urn:iki:onboard:invite:{invite}> ;\n"
        f"    onb:certificate {literal} ;\n"
        f'    onb:fingerprint "{fingerprint}" ;\n'
        f'    onb:proof "{proof_hex}" .\n'
    )


def add_parser(commands) -> None:
    """Add the standalone lobby enrollment command."""
    p = commands.add_parser(
        "onboard",
        help="enroll through the lobby with a pairing code",
        epilog="Exit codes: 0 enrolled, 1 denied or revoked, 2 local problem, "
        "3 awaiting approval, 4 pin or protocol refused, 5 unreachable.",
    )
    p.add_argument("--door", required=True, help="the door: quic://HOST:PORT or HOST:PORT")
    p.add_argument("--pin", required=True, help="the door's server-certificate SHA-256")
    p.add_argument("--server-cert", help="the door's PEM certificate (required on the first run)")
    p.add_argument("--invite", required=True, help="the invite id")
    p.add_argument("--code", required=True, help="the pairing code (never stored)")
    who = p.add_mutually_exclusive_group()
    who.add_argument("--name", help="the actor (default: this machine's short host name)")
    who.add_argument("--cert-dir", help="identity and config directory instead of the named set")
    p.add_argument("--once", action="store_true", help="poll once; exit 3 while awaiting approval")
    p.add_argument("--max-wait", type=float, default=86400, help="polling deadline in seconds")
    p.add_argument("--force", action="store_true", help="replace a differing server pin or config")


def configured_door(directory: Path) -> str:
    """The enrolled door, or the original default for a manually prepared cert set."""
    path = directory / "config.toml"
    if not path.exists():
        return door.DOOR
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    url = config.get("door")
    if not isinstance(url, str):
        raise ValueError(f"{path}: expected a door URL")
    quic.parse_door(url)
    return url


def _values(node, local, namespace=ONB, prefix="onb"):
    for key in (local, f"{prefix}:{local}", namespace + local):
        if key in node:
            value = node[key]
            return value if isinstance(value, list) else [value]
    return []


def _text(value):
    return value.get("@value", value.get("@id")) if isinstance(value, dict) else value


def current(nodes: list[dict], fingerprint: str, now: datetime) -> list[dict]:
    """Current entitlements for this certificate; expired replacements still supersede."""
    found = []
    for node in nodes:
        types = node.get("@type", [])
        types = types if isinstance(types, list) else [types]
        if not any(t in ("Entitlement", "onb:Entitlement", ONB + "Entitlement") for t in types):
            continue
        if fingerprint not in [_text(v) for v in _values(node, "fingerprint")]:
            continue
        found.append(node)
    superseded = {
        _text(v) for n in found for v in _values(n, "supersedes", namespace=IK, prefix="ik")
    }
    live = []
    for node in found:
        if node.get("@id") in superseded:
            continue
        expires = _values(node, "expires")
        if expires:
            try:
                end = datetime.fromisoformat(_text(expires[0]).replace("Z", "+00:00"))
                if end.tzinfo is None or end <= now:
                    continue
            except (ValueError, TypeError, AttributeError):
                continue
        live.append(node)
    return live


def _poll(d, fingerprint):
    nodes, after = [], 0
    while True:
        try:
            answer = d.source(
                "urn:layer:grants:entries",
                type=ONB + "Entitlement",
                after=str(after),
                limit=str(PAGE),
                **{"as": "application/ld+json"},
            )
        except ikigai.DeniedError:
            return []  # the lobby can post but cannot read grants until approval
        doc = json.loads(answer.data)
        page = doc.get("@graph") if isinstance(doc, dict) else None
        if not isinstance(page, list) or any(not isinstance(n, dict) for n in page):
            raise ValueError("the grants layer did not return a JSON-LD graph")
        nodes.extend(page)
        if len(page) < PAGE:
            return current(nodes, fingerprint, datetime.now(UTC))
        seqs = [_text(v) for n in page for v in _values(n, "seq", IK, "ik")]
        next_after = max(map(int, seqs), default=after)
        if next_after <= after:
            raise ValueError("the grants layer's pagination did not advance")
        after = next_after


def _put(path: Path, text: str, force: bool) -> None:
    if os.path.lexists(path):
        if path.read_text(encoding="utf-8") == text:
            return
        if not force:
            raise ValueError(f"{path} differs: kept it; --force replaces it")
    certs._write(path, text, force=os.path.lexists(path))


def run(args, say) -> int:
    """Generate, pin, request, poll, and save config; return 3 while awaiting approval."""
    url = args.door if args.door.startswith("quic://") else "quic://" + args.door
    quic.parse_door(url)
    pin = args.pin.replace(":", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError("--pin must be a SHA-256 fingerprint (64 hex digits)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.invite):
        raise ValueError(
            "--invite must be an invite id (letters, digits, dot, underscore or hyphen)"
        )
    if not args.code or any(c.isspace() for c in args.code):
        raise ValueError("--code must be the pairing code with no spaces")
    if not math.isfinite(args.max_wait) or args.max_wait < 0:
        raise ValueError("--max-wait must be a finite, nonnegative number of seconds")
    name = args.name or socket.gethostname().split(".")[0].lower()
    if args.cert_dir:
        directory = Path(args.cert_dir).expanduser().resolve()
    else:
        door.layer_name(name)
        directory = door.default_cert_dir(name).resolve()

    # Check the operator's certificate before generating an identity or dialing.
    target = directory / "server.crt"
    source = Path(args.server_cert).expanduser() if args.server_cert else target
    pem = source.read_text(encoding="utf-8")
    actual = x509.load_pem_x509_certificate(pem.encode()).fingerprint(hashes.SHA256()).hex()
    if actual != pin:
        raise quic.TrustError(f"{source} does not match --pin: nothing was sent")
    if not directory.exists():
        directory.mkdir(parents=True, mode=0o700)
    crt, key = directory / "client.crt", directory / "client.key"
    if os.path.lexists(crt) != os.path.lexists(key):
        raise ValueError(
            f"{directory} needs both client.crt and client.key; move it aside and retry"
        )
    _put(target, pem, args.force)
    if not os.path.lexists(crt):
        certs.generate(directory)
    loaded = quic.CertSet.from_dir(directory).load()  # includes the existing key/pair check
    fingerprint = loaded.fingerprint
    say(f"fingerprint: {fingerprint}")

    state_path = directory / "onboard.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if not isinstance(state, dict):
        raise ValueError(f"{state_path} must hold a JSON object")
    scope = {"door": url, "pin": pin, "invite": args.invite, "fingerprint": fingerprint}
    seen = all(state.get(k) == v for k, v in scope.items()) and state.get("request")
    if not seen:
        body = request_turtle(
            args.invite, crt.read_text(encoding="utf-8"), fingerprint, proof(args.code, fingerprint)
        )
        with door.connect(url, directory, timeout=30) as d:
            answer = d.sink("urn:layer:enroll:say", body, **{"as": "application/json"})
        reply = json.loads(answer.data)
        entry = reply.get("@id") if isinstance(reply, dict) else None
        if not isinstance(entry, str) or not entry.startswith("urn:layer:enroll:entry:"):
            raise ValueError("the enrollment reply did not name the posted entry")
        certs._write(
            state_path,
            json.dumps({**scope, "request": entry}) + "\n",
            force=os.path.lexists(state_path),
        )
        say(f"request posted: {entry}; waiting for controller approval")
    else:
        say(f"request already posted: {state['request']}; resuming approval check")
    deadline, delay = time.monotonic() + args.max_wait, 5.0
    while True:
        # Authorization is minted at connection time. A fresh connection sees approval
        # (or revocation) instead of retaining the lobby's original capability.
        with door.connect(url, directory, timeout=30) as d:
            live = _poll(d, fingerprint)
        if live:
            caps = [_text(v) for n in live for v in _values(n, "layerGrant")]
            if not caps:
                raise ikigai.DeniedError(
                    "current entitlement grants no layer capabilities (revoked or gonk only)"
                )
            if any(not isinstance(c, str) or not _CAP.fullmatch(c) for c in caps):
                raise ValueError("onb:layerGrant must be a urn:cap:layer:VERB:LAYER capability IRI")
            config = f"door = {json.dumps(url)}\n"
            _put(directory / "config.toml", config, args.force)
            say(f"enrolled: {', '.join(sorted(set(caps)))}")
            say(f"client configuration: {directory / 'config.toml'}")
            return 0
        remaining = deadline - time.monotonic()
        if args.once or remaining <= 0:
            say("not enrolled yet: no current entitlement; run onboard again to resume")
            return 3
        wait = min(delay, remaining)
        say(f"not enrolled yet: checking again in {wait:g} seconds")
        time.sleep(wait)
        delay = min(delay * 2, 300.0)
