"""The layer door, as this client reaches it: one QUIC connection, three routed prefixes.

Everything the client asks for lives under ``urn:layer:`` (each layer's actions),
``urn:layers:`` (the multiplexed feed and ``urn:layers:me``) or ``urn:collab:`` (a
collaboration's manifest and roster), which is what the Rust cli's
``--override urn:layer:,urn:layers:,urn:collab:=quic://…`` routes to a door. This client
routes the same three and nothing else: a name outside them is refused here, before it is
sent anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from ikigai import quic

#: The door this client talks to unless ``--door`` says otherwise.
DOOR = "quic://ikigai-rs.dev:4434"

#: The prefixes routed to the door, as the cli's one multi-prefix ``--override`` routes them.
ROUTED = ("urn:layer:", "urn:layers:", "urn:collab:")

#: Where an actor's cert set lives by default: ``~/.ikigai/layers-client/<name>``.
CERT_ROOT = Path("~/.ikigai/layers-client")

# The layer-name rule (ikigai-layers): lowercase [a-z0-9-], at most 24 characters.
_LAYER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")


def layer_name(name: str) -> str:
    """``name`` if it is a layer name (lowercase ``[a-z0-9-]``, at most 24 characters, no
    leading or trailing ``-``), else ``ValueError``.

    >>> layer_name("talk")
    'talk'
    """
    if not _LAYER.match(name):
        raise ValueError(f"`{name}` is not a layer name (lowercase a-z, 0-9 and -, 1 to 24)")
    return name


def default_cert_dir(name: str) -> Path:
    """``~/.ikigai/layers-client/<name>``, expanded."""
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"`{name}` is not an actor name")
    return (CERT_ROOT / name).expanduser()


def routed(iri: str) -> str:
    """``iri`` if one of :data:`ROUTED` prefixes it, else ``ValueError``."""
    if not iri.startswith(ROUTED):
        raise ValueError(f"{iri} is not routed to the layer door (only {', '.join(ROUTED)})")
    return iri


class Door:
    """A connected layer door: the verbs this client uses, each checked against
    :data:`ROUTED` before anything is sent."""

    def __init__(self, client: quic.QuicClient):
        """Adopt a connected client; prefer :func:`connect`."""
        self.client = client

    @property
    def fingerprint(self) -> str:
        """This actor's certificate fingerprint, as the door enrolls it."""
        return self.client.fingerprint

    def source(self, iri: str, **args):
        """Read ``iri`` at the door."""
        return self.client.source(routed(iri), **args)

    def sink(self, iri: str, content: str | bytes, **args):
        """Write ``content`` to ``iri`` at the door."""
        return self.client.sink(routed(iri), content, **args)

    def delete(self, iri: str, **args):
        """Delete at ``iri`` (a take, for a tuplespace)."""
        return self.client.delete(routed(iri), **args)

    def close(self) -> None:
        """Close the connection."""
        self.client.close()

    def __enter__(self) -> Door:
        """``with connect(…) as door:``."""
        return self

    def __exit__(self, *exc) -> None:
        """Close on leaving the block."""
        self.close()


def connect(door: str, cert_dir: Path, *, timeout: float | None = None) -> Door:
    """Connect to ``door`` with the cert set in ``cert_dir``. ``timeout`` bounds each call
    (``None``: the transport's default, 300 s)."""
    kwargs = {} if timeout is None else {"timeout": timeout}
    return Door(quic.connect(door, cert_dir, **kwargs))
