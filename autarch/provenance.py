"""Provenance — cryptographically signed, non-repudiable action records.

The hash chain (see `memory.py`) proves a record was not *altered*. Provenance
goes further: it proves *who* produced a record, unforgeably.

Each node holds an **Ed25519** keypair. Its node identity is *derived from* its
public key (`node_ + sha256(pubkey)[:16]`), so the identity is cryptographically
bound to the key — you cannot claim to be a node without holding its private key.
Every action's seal is signed; any party with the public key can verify both that
the record is intact and that it was authored by that exact identity. A realm
member who shares the symmetric realm key still cannot forge a record attributed
to another node, because they do not hold that node's private signing key.

Requires the optional `cryptography` package (`pip install autarch[crypto]`).
Without it, signing is a no-op and records are simply unsigned — the rest of the
system is unaffected.

Honest boundary: provenance proves *authorship* (the holder of key K signed this,
and the claimed node id is bound to K). It does not, by itself, prove that K is an
*authorized* member of a realm — that is a separate trust-management concern
(recording the set of member public keys), a natural next layer.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import SecretError

_SCRYPT_N = 2 ** 14  # CPU/memory cost — secure yet fast enough for interactive use
_SCRYPT_R = 8
_SCRYPT_P = 1


def available() -> bool:
    """Whether Ed25519 signing is usable (the `cryptography` package present)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: F401
            Ed25519PrivateKey,
        )
        return True
    except Exception:
        return False


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from a passphrase via scrypt (memory-hard KDF)."""
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    kdf = Scrypt(salt=salt, length=32, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def _seal_secret(plaintext_hex: str, passphrase: str) -> dict:
    """Encrypt a secret string under a passphrase (scrypt + AES-256-GCM)."""
    if not available():
        raise SecretError(
            "encrypting a key at rest requires the `cryptography` package",
            context={"fix": "pip install autarch[crypto]"},
        )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_key(passphrase, salt)
    ct = AESGCM(key).encrypt(nonce, plaintext_hex.encode("utf-8"), None)
    return {"kdf": "scrypt", "salt": salt.hex(), "nonce": nonce.hex(), "ct": ct.hex()}


def _open_secret(enc: dict, passphrase: Optional[str]) -> str:
    """Decrypt a sealed secret. Raises SecretError on a wrong/missing passphrase."""
    if not passphrase:
        raise SecretError(
            "this identity's private key is encrypted; a passphrase is required",
            context={"node": enc.get("node_id", "")},
        )
    if not available():
        raise SecretError("decrypting requires the `cryptography` package")
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        key = _derive_key(passphrase, bytes.fromhex(enc["salt"]))
        pt = AESGCM(key).decrypt(bytes.fromhex(enc["nonce"]), bytes.fromhex(enc["ct"]), None)
        return pt.decode("utf-8")
    except (InvalidTag, ValueError) as exc:
        raise SecretError("wrong passphrase or corrupted key material") from exc


def derive_node_id(public_hex: str) -> str:
    """A node id cryptographically bound to its public key."""
    digest = hashlib.sha256(bytes.fromhex(public_hex)).hexdigest()
    return "node_" + digest[:16]


def verify_signature(public_hex: str, data: bytes, signature_hex: str) -> bool:
    """True iff `signature_hex` is a valid Ed25519 signature of `data` by `public_hex`."""
    if not public_hex or not signature_hex:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        pub.verify(bytes.fromhex(signature_hex), data)
        return True
    except (InvalidSignature, ValueError):
        return False


@dataclass
class NodeIdentity:
    """A node's signing identity. Holds the private key only on its own node."""

    node_id: str
    public_hex: str
    _private_hex: Optional[str] = None  # None => verify-only (no signing)

    @classmethod
    def create(cls) -> "NodeIdentity":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.generate()
        priv = sk.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        pub = sk.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        public_hex = pub.hex()
        return cls(derive_node_id(public_hex), public_hex, priv.hex())

    @property
    def can_sign(self) -> bool:
        return self._private_hex is not None

    def sign(self, data: bytes) -> str:
        """Return a hex Ed25519 signature of `data` (empty string if verify-only)."""
        if not self._private_hex:
            return ""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self._private_hex))
        return sk.sign(data).hex()

    def public_identity(self) -> "NodeIdentity":
        """A verify-only copy safe to share (no private key)."""
        return NodeIdentity(self.node_id, self.public_hex, None)

    # -- persistence ------------------------------------------------------
    def save(self, workspace, passphrase: Optional[str] = None) -> Path:
        """Persist this identity.

        With a `passphrase`, the private key is **encrypted at rest** (scrypt-derived
        key + AES-256-GCM) — the recommended production path. Without one, the key is
        stored in plaintext and clearly flagged, so an operator can see the risk.
        """
        path = Path(workspace) / ".autarch" / "identity.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"node_id": self.node_id, "public": self.public_hex}

        if passphrase:
            record["enc"] = _seal_secret(self._private_hex or "", passphrase)
        else:
            record["private"] = self._private_hex
            record["private_plaintext"] = True  # honest flag: not encrypted at rest

        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        try:  # best-effort owner-only perms (POSIX); harmless/no-op on Windows
            path.chmod(0o600)
        except Exception:
            pass
        return path

    @classmethod
    def load(cls, workspace, passphrase: Optional[str] = None) -> Optional["NodeIdentity"]:
        path = Path(workspace) / ".autarch" / "identity.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if "enc" in data:
            private = _open_secret(data["enc"], passphrase)
            return cls(data["node_id"], data["public"], private)
        return cls(data["node_id"], data["public"], data.get("private"))

    @classmethod
    def load_or_create(cls, workspace, passphrase: Optional[str] = None) -> Optional["NodeIdentity"]:
        """Load this workspace's identity, creating one if crypto is available."""
        existing = cls.load(workspace, passphrase=passphrase)
        if existing is not None:
            return existing
        if not available():
            return None
        identity = cls.create()
        identity.save(workspace, passphrase=passphrase)
        return identity

    def is_encrypted_at_rest(self, workspace) -> bool:
        path = Path(workspace) / ".autarch" / "identity.json"
        if not path.exists():
            return False
        return "enc" in json.loads(path.read_text(encoding="utf-8"))
