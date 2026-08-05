"""Provenance unit tests — Ed25519 identity, key-bound ids, signatures."""
import pytest

from autarch.provenance import (
    NodeIdentity,
    available,
    derive_node_id,
    verify_signature,
)

crypto = pytest.importorskip("cryptography")  # the whole module needs Ed25519


def test_available_true_with_cryptography():
    assert available() is True


def test_create_identity_is_key_bound():
    ident = NodeIdentity.create()
    assert ident.can_sign is True
    # The node id is derived from the public key — not random.
    assert ident.node_id == derive_node_id(ident.public_hex)
    assert ident.node_id.startswith("node_")


def test_sign_and_verify_roundtrip():
    ident = NodeIdentity.create()
    sig = ident.sign(b"hello world")
    assert sig  # non-empty hex
    assert verify_signature(ident.public_hex, b"hello world", sig) is True


def test_signature_rejects_wrong_data():
    ident = NodeIdentity.create()
    sig = ident.sign(b"original")
    assert verify_signature(ident.public_hex, b"tampered", sig) is False


def test_signature_rejects_wrong_key():
    a, b = NodeIdentity.create(), NodeIdentity.create()
    sig = a.sign(b"data")
    assert verify_signature(b.public_hex, b"data", sig) is False


def test_two_identities_differ():
    assert NodeIdentity.create().node_id != NodeIdentity.create().node_id


def test_public_identity_cannot_sign():
    ident = NodeIdentity.create()
    pub = ident.public_identity()
    assert pub.node_id == ident.node_id
    assert pub.public_hex == ident.public_hex
    assert pub.can_sign is False
    assert pub.sign(b"x") == ""


def test_save_and_load(tmp_path):
    ident = NodeIdentity.create()
    ident.save(tmp_path)
    loaded = NodeIdentity.load(tmp_path)
    assert loaded.node_id == ident.node_id
    assert loaded.public_hex == ident.public_hex
    assert loaded.can_sign is True
    # A loaded identity produces signatures the original key verifies.
    assert verify_signature(ident.public_hex, b"m", loaded.sign(b"m")) is True


def test_load_or_create_is_stable(tmp_path):
    first = NodeIdentity.load_or_create(tmp_path)
    second = NodeIdentity.load_or_create(tmp_path)
    assert first.node_id == second.node_id  # persisted, not regenerated


def test_load_absent_is_none(tmp_path):
    assert NodeIdentity.load(tmp_path) is None


def test_verify_signature_empty_inputs():
    assert verify_signature("", b"x", "deadbeef") is False
    assert verify_signature("aa" * 32, b"x", "") is False
