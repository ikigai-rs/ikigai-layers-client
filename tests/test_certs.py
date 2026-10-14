"""``cert generate``: the Rust cli's layout and PEM form, no server key, the fingerprint."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from ikigai import quic

from ikigai_layers_client import certs
from ikigai_layers_client.cli import main


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_pair_is_p256_san_ikigai_in_pem_mode_0600(tmp_path):
    d = tmp_path / "actor"
    fp = certs.generate(d)
    assert sorted(p.name for p in d.iterdir()) == ["client.crt", "client.key"]
    assert mode(d) == 0o700
    assert mode(d / "client.crt") == 0o600 and mode(d / "client.key") == 0o600
    crt, key = (d / "client.crt").read_text(), (d / "client.key").read_text()
    assert crt.startswith("-----BEGIN CERTIFICATE-----\n")
    assert key.startswith("-----BEGIN PRIVATE KEY-----\n")  # PKCS#8, as rcgen writes
    cert = x509.load_pem_x509_certificate(crt.encode())
    assert isinstance(cert.public_key(), ec.EllipticCurvePublicKey)
    assert cert.public_key().curve.name == "secp256r1"
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["ikigai"]
    assert cert.issuer == cert.subject  # self-signed
    assert cert.not_valid_before_utc.year == 1975 and cert.not_valid_after_utc.year == 4096
    private = serialization.load_pem_private_key(key.encode(), None)
    assert private.public_key().public_numbers() == cert.public_key().public_numbers()
    assert fp == quic.fingerprint(cert.public_bytes(serialization.Encoding.DER))


def test_no_server_key_is_ever_written(tmp_path):
    d = tmp_path / "actor"
    certs.generate(d)
    certs.generate(d, force=True)
    assert not (d / "server.key").exists() and not (d / "server.crt").exists()


def test_the_fingerprint_is_what_openssl_and_sha256sum_print(tmp_path):
    openssl = shutil.which("openssl")
    summer = shutil.which("sha256sum") or shutil.which("shasum")
    if openssl is None or summer is None:
        pytest.skip("needs openssl and sha256sum (or shasum)")
    d = tmp_path / "actor"
    fp = certs.generate(d)
    summing = [summer] if summer.endswith("sha256sum") else [summer, "-a", "256"]
    der = subprocess.run(
        [openssl, "x509", "-in", str(d / "client.crt"), "-outform", "DER"],
        capture_output=True,
        check=True,
    ).stdout
    printed = subprocess.run(summing, input=der, capture_output=True, check=True).stdout
    assert printed.decode().split()[0] == fp


def test_an_existing_pair_is_refused_whole_and_force_replaces_it(tmp_path):
    d = tmp_path / "actor"
    first = certs.generate(d)
    before = (d / "client.key").read_text()
    with pytest.raises(certs.CertExists, match="nothing was written"):
        certs.generate(d)
    assert (d / "client.key").read_text() == before
    second = certs.generate(d, force=True)
    assert second != first and (d / "client.key").read_text() != before
    assert mode(d / "client.key") == 0o600
    assert [p.name for p in d.iterdir() if p.name.startswith(".")] == []


def test_a_lone_existing_key_refuses_too(tmp_path):
    d = tmp_path / "actor"
    d.mkdir()
    (d / "client.key").write_text("old")
    with pytest.raises(certs.CertExists):
        certs.generate(d)
    assert not (d / "client.crt").exists()


def test_a_link_at_the_path_is_replaced_never_written_through(tmp_path):
    d = tmp_path / "actor"
    d.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    os.symlink(victim, d / "client.key")
    with pytest.raises(certs.CertExists):
        certs.generate(d)
    certs.generate(d, force=True)
    assert victim.read_text() == "keep me" and not (d / "client.key").is_symlink()


def test_an_existing_directory_keeps_its_mode(tmp_path):
    d = tmp_path / "shared"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    certs.generate(d)
    assert mode(d) == 0o755


def test_the_command_prints_the_fingerprint_and_the_next_steps(tmp_path, capsys):
    assert main(["cert", "generate", "--dir", str(tmp_path / "a")]) == 0
    out = capsys.readouterr().out
    fp = quic.fingerprint_of_pem((tmp_path / "a" / "client.crt").read_text())
    assert f"fingerprint: {fp}" in out
    assert "server.crt" in out and "never client.key" in out
    assert main(["cert", "generate", "--dir", str(tmp_path / "a")]) == 2


def test_name_writes_under_the_default_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["cert", "generate", "--name", "someone"]) == 0
    assert (tmp_path / ".ikigai/layers-client/someone/client.crt").exists()
