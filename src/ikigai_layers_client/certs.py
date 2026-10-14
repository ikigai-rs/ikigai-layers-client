"""``ikigai-layers cert generate``: one actor's client identity, in the Rust cli's layout.

A self-signed ECDSA P-256 certificate with the subject alternative name ``ikigai`` and its
private key, written as ``client.crt`` (PEM ``CERTIFICATE``) and ``client.key`` (PEM PKCS#8
``PRIVATE KEY``), both mode 0600 in a directory of mode 0700: the form and the layout
``ikigai cert generate --dir DIR`` writes, so the set later works with the Rust cli unchanged
(``--cert-dir DIR``). The validity window is rcgen's default (1975 to 4096), as the Rust
generator's: trust is the pinned certificate itself, never its dates.

**No server key is ever written.** The other half of a set is the door's ``server.crt``,
which its operator sends; nothing on this side ever holds a key it does not need.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from ikigai.quic import fingerprint

# rcgen's default validity, which `ikigai cert generate` writes too.
NOT_BEFORE = datetime.datetime(1975, 1, 1, tzinfo=datetime.UTC)
NOT_AFTER = datetime.datetime(4096, 1, 1, tzinfo=datetime.UTC)

# The one name in the certificate, as the Rust generator's. Cosmetic: nothing checks it.
SUBJECT_ALT_NAME = "ikigai"


class CertExists(Exception):
    """A file of the pair is already there and ``force`` was not given; nothing was written."""


def identity() -> tuple[str, str, str]:
    """A fresh pair: the certificate PEM, the PKCS#8 key PEM, and the certificate's
    fingerprint (lowercase hex SHA-256 of its DER, the id a door enrolls)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ikigai layers client")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOT_BEFORE)
        .not_valid_after(NOT_AFTER)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(SUBJECT_ALT_NAME)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem, fingerprint(certificate.public_bytes(serialization.Encoding.DER))


def generate(directory: Path, *, force: bool = False) -> str:
    """Write ``client.crt`` and ``client.key`` into ``directory`` (created mode 0700) and
    return the certificate's fingerprint. Refuses, writing nothing, when either file (or a
    link) is already there, unless ``force``, which replaces both whole. A directory that
    already exists keeps its mode: only one this call creates is made 0700."""
    directory = directory.expanduser()
    if not directory.exists():
        directory.mkdir(parents=True)
        os.chmod(directory, 0o700)
    crt, key = directory / "client.crt", directory / "client.key"
    if not force:
        # Both checked before either is written: a new certificate beside an old key (or
        # the reverse) is a pair that matches nothing, worse than refusing.
        existing = [str(p) for p in (crt, key) if os.path.lexists(p)]
        if existing:
            raise CertExists(
                f"{', '.join(existing)} already exist(s): nothing was written "
                "(--force replaces the pair)"
            )
    cert_pem, key_pem, fp = identity()
    # The key first: a certificate on disk always has its key beside it.
    _write(key, key_pem, force)
    _write(crt, cert_pem, force)
    return fp


def _write(path: Path, text: str, force: bool) -> None:
    """Create ``path`` with mode 0600 from its first byte. Without ``force`` the create is
    exclusive (it refuses a file or a link already there); with it, a private temporary is
    written beside the path and renamed over it, replacing a link rather than writing
    through it, as the Rust generator does."""
    target = path
    if force:
        target = path.with_name(f".{path.name}.partial-{os.getpid()}")
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        os.unlink(target)
        raise
    if force:
        os.replace(target, path)
