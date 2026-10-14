"""Usage problems are refused here, before anything is dialed, with exit 2."""

from __future__ import annotations

import pytest

from ikigai_layers_client import door
from ikigai_layers_client.cli import main


@pytest.fixture
def no_dial(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("dialed the door")

    monkeypatch.setattr(door, "connect", refuse)


@pytest.mark.parametrize(
    "argv",
    [
        ["me"],  # no actor
        ["me", "--name", "a", "--door", "https://example.invalid"],
        ["say", "Talk", "hi", "--name", "a"],
        ["read", "talk-", "--name", "a"],
        ["follow", "--name", "a", "--wait", "61"],
        ["follow", "--name", "a", "--layers", ","],
        ["follow", "--name", "a", "--reset", "Bad"],
    ],
)
def test_usage_is_refused_before_dialing(no_dial, argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith("ikigai-layers: ")


def test_only_the_three_prefixes_are_routed():
    assert door.routed("urn:layer:talk:say") == "urn:layer:talk:say"
    assert door.routed("urn:layers:me") and door.routed("urn:collab:demo")
    for iri in ("urn:iki:fn:toUpper", "urn:layerx:", "urn:file:secret"):
        with pytest.raises(ValueError, match="not routed"):
            door.routed(iri)


def test_the_default_door_and_cert_root():
    assert door.DOOR == "quic://ikigai-rs.dev:4434"
    assert str(door.default_cert_dir("a")).endswith("/.ikigai/layers-client/a")
    with pytest.raises(ValueError):
        door.default_cert_dir("../x")
