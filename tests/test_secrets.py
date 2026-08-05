"""Secrets-at-rest tests — the private key must never sit in plaintext."""
import json

import pytest

from autarch import NodeIdentity, SecretError, verify_signature

pytest.importorskip("cryptography")


def test_plaintext_save_is_flagged(tmp_path):
    ident = NodeIdentity.create()
    ident.save(tmp_path)  # no passphrase
    data = json.loads((tmp_path / ".autarch" / "identity.json").read_text())
    assert data.get("private_plaintext") is True
    assert "private" in data  # plaintext path (back-compat) clearly marked


def test_encrypted_save_has_no_plaintext_key(tmp_path):
    ident = NodeIdentity.create()
    ident.save(tmp_path, passphrase="correct horse")
    raw = (tmp_path / ".autarch" / "identity.json").read_text()
    assert ident._private_hex not in raw          # the secret is absent on disk
    data = json.loads(raw)
    assert "enc" in data and "private" not in data
    assert data["enc"]["kdf"] == "scrypt"


def test_encrypted_roundtrip(tmp_path):
    ident = NodeIdentity.create()
    ident.save(tmp_path, passphrase="s3cret")
    loaded = NodeIdentity.load(tmp_path, passphrase="s3cret")
    assert loaded.can_sign is True
    # The recovered key really signs verifiably.
    assert verify_signature(loaded.public_hex, b"m", loaded.sign(b"m")) is True


def test_wrong_passphrase_raises(tmp_path):
    NodeIdentity.create().save(tmp_path, passphrase="right")
    with pytest.raises(SecretError):
        NodeIdentity.load(tmp_path, passphrase="wrong")


def test_missing_passphrase_raises(tmp_path):
    NodeIdentity.create().save(tmp_path, passphrase="right")
    with pytest.raises(SecretError):
        NodeIdentity.load(tmp_path)  # encrypted but no passphrase given


def test_is_encrypted_at_rest(tmp_path):
    ident = NodeIdentity.create()
    ident.save(tmp_path)
    assert ident.is_encrypted_at_rest(tmp_path) is False
    ident.save(tmp_path, passphrase="x")
    assert ident.is_encrypted_at_rest(tmp_path) is True


def test_plaintext_back_compat_loads(tmp_path):
    # A pre-Phase-B identity.json (plaintext, no flag) must still load.
    path = tmp_path / ".autarch"
    path.mkdir(parents=True)
    ident = NodeIdentity.create()
    (path / "identity.json").write_text(json.dumps({
        "node_id": ident.node_id, "public": ident.public_hex, "private": ident._private_hex,
    }))
    loaded = NodeIdentity.load(tmp_path)
    assert loaded.can_sign is True
    assert loaded.node_id == ident.node_id


def test_load_or_create_with_passphrase_is_stable(tmp_path):
    first = NodeIdentity.load_or_create(tmp_path, passphrase="pw")
    second = NodeIdentity.load_or_create(tmp_path, passphrase="pw")
    assert first.node_id == second.node_id  # persisted + decrypted, not regenerated
