"""Open enrollment into public read-only feeds from an HTTPS discovery descriptor.

Only stdlib HTTPS and existing certificate/QUIC helpers are used. HTTPS authenticates
the discovery document; its SHA-256 pins the DER server certificate used by QUIC.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import ikigai
from ikigai import quic

from . import certs, door, message, onboard

DESCRIPTOR = "/.well-known/ikigai/layers.json"
MAX_DOCUMENT = 1024 * 1024


def _https(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(
            "discovery and certificate URLs must be HTTPS URLs without credentials or fragments"
        )
    return url


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Keep redirects on HTTPS, including the redirects before the final response."""
        _https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str) -> bytes:
    """Fetch a bounded HTTPS document using system trust and a finite timeout."""
    _https(url)
    try:
        opener = urllib.request.build_opener(_HTTPSRedirect())
        with opener.open(url, timeout=30) as response:
            _https(response.geturl())
            data = response.read(MAX_DOCUMENT + 1)
    except urllib.error.URLError as exc:
        raise ikigai.ConnectionLost(f"HTTPS discovery did not answer: {exc.reason}") from exc
    if len(data) > MAX_DOCUMENT:
        raise ValueError("discovery document or certificate exceeds 1 MiB")
    return data


def add_parser(commands) -> None:
    """Add the standalone open-join command."""
    parser = commands.add_parser("join", help="join public read-only feeds without an invite")
    parser.add_argument(
        "host",
        nargs="?",
        default="https://ikigai-rs.dev",
        help="HTTPS discovery host (default: https://ikigai-rs.dev)",
    )
    parser.add_argument("feeds", nargs="*", help="public feed names (default: all public feeds)")
    who = parser.add_mutually_exclusive_group()
    who.add_argument("--name", help="the actor (default: this machine's short host name)")
    who.add_argument("--cert-dir", help="identity and configuration directory")
    parser.add_argument("--json", action="store_true", help="print the synchronous join answer")


def descriptor(host: str) -> tuple[dict, str]:
    """Validate the discovery location and fetch its JSON object."""
    _https(host)
    parsed = urllib.parse.urlsplit(host)
    if parsed.path not in ("", "/") or parsed.query:
        raise ValueError("give the HTTPS host only, without a path or query")
    url = host.rstrip("/") + DESCRIPTOR
    document = json.loads(fetch(url))
    if not isinstance(document, dict):
        raise ValueError("the discovery descriptor must be a JSON object")
    return document, url


def run(args, say) -> int:
    """Verify discovery, reuse or generate the named identity, join, and save its door."""
    # With the optional host omitted, the first positional is a feed name.
    if "://" not in args.host:
        args.feeds = [args.host, *args.feeds]
        args.host = "https://ikigai-rs.dev"
    for name in args.feeds:
        door.layer_name(name)
    doc, location = descriptor(args.host)
    url, lobby, server = doc.get("door"), doc.get("join"), doc.get("server_cert")
    if not isinstance(url, str) or not isinstance(lobby, str) or not isinstance(server, dict):
        raise ValueError("descriptor needs door, join and server_cert")
    quic.parse_door(url)
    if not re.fullmatch(r"urn:layer:[a-z0-9-]+:say", lobby):
        raise ValueError("descriptor join must name a layer's say endpoint")
    door.layer_name(lobby.split(":")[2])
    pin, cert_url = server.get("sha256"), server.get("url")
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pin):
        raise ValueError("descriptor server_cert.sha256 must be 64 hex digits")
    if not isinstance(cert_url, str):
        raise ValueError("descriptor server_cert.url must name the server certificate")
    public = doc.get("public_layers")
    if not isinstance(public, list) or any(not isinstance(p, dict) for p in public):
        raise ValueError("descriptor public_layers must be a list of layer objects")
    available = {}
    for layer in public:
        if not isinstance(layer.get("name"), str):
            raise ValueError("each public layer must have a string name")
        name = door.layer_name(layer["name"])
        if name in available:
            raise ValueError(f"descriptor names layer `{name}` twice")
        available[name] = layer
    selected = (
        list(dict.fromkeys(args.feeds))
        if args.feeds
        else [
            name
            for name, layer in available.items()
            if layer.get("type") in ("ik:Feed", message.IK + "Feed")
            and layer.get("read_only") is True
        ]
    )
    for name in selected:
        door.layer_name(name)
        layer = available.get(name, {})
        if (
            layer.get("type") not in ("ik:Feed", message.IK + "Feed")
            or layer.get("read_only") is not True
        ):
            raise ValueError(f"`{name}` is not a public read-only feed in the descriptor")
    if not selected:
        raise ValueError("the descriptor offers no public read-only feeds")
    pem = fetch(urllib.parse.urljoin(location, cert_url)).decode("ascii")
    # Hash the decoded certificate (DER), matching the existing --pin convention.
    actual = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    if actual != pin.lower():
        raise quic.TrustError("server.crt does not match the descriptor sha256: nothing was sent")
    name = args.name or socket.gethostname().split(".")[0].lower()
    directory = Path(args.cert_dir).expanduser() if args.cert_dir else door.default_cert_dir(name)
    crt, key = directory / "client.crt", directory / "client.key"
    if os.path.lexists(crt) != os.path.lexists(key):
        raise ValueError(
            f"{directory} needs both client.crt and client.key; move it aside and retry"
        )
    config = f"door = {json.dumps(url)}\n"
    # A join cannot silently repoint an existing identity to a different door or certificate.
    for path, expected in ((directory / "server.crt", pem), (directory / "config.toml", config)):
        if os.path.lexists(path) and path.read_text(encoding="utf-8") != expected:
            raise ValueError(f"{path} differs: kept it; use a separate --name for this door")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    onboard._put(directory / "server.crt", pem, False)
    if not os.path.lexists(crt):
        certs.generate(directory)
    loaded = quic.CertSet.from_dir(directory).load()
    body = (
        f"@prefix onb: <{onboard.ONB}> .\n"
        f"<urn:iki:onboard:join:{loaded.fingerprint}> a onb:JoinRequest ;\n"
        f"    onb:certificate {message.literal(crt.read_text(encoding='utf-8'))} ;\n"
        f"    onb:fingerprint {message.literal(loaded.fingerprint)} ;\n"
        "    onb:layer " + ", ".join(f"<urn:layer:{name}>" for name in selected) + " .\n"
    )
    with door.connect(url, directory, timeout=30) as d:
        answer = d.sink(lobby, body, **{"as": "application/json"})
    reply = json.loads(answer.data)
    grants = reply.get("granted") if isinstance(reply, dict) else None
    allowed = {f"urn:cap:layer:{verb}:{name}" for name in selected for verb in ("read", "feed")}
    if (
        not isinstance(grants, list)
        or not grants
        or any(not isinstance(g, str) or g not in allowed for g in grants)
    ):
        raise ValueError(
            "join reply must grant only read/feed capabilities for the requested feeds"
        )
    onboard._put(directory / "config.toml", config, False)
    if args.json:
        say(json.dumps(reply))
    else:
        say(f"fingerprint: {loaded.fingerprint}")
        say(f"joined: {', '.join(sorted(set(grants)))}")
        say(f"client configuration: {directory / 'config.toml'}")
    return 0
