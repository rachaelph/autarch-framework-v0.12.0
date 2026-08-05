"""The Mesh — one identity, one policy, many nodes.

Your phone, laptop, and home hub are nodes of a single Autarch realm. They
share an identity (a realm key), a policy set, and — through local-first,
encrypted sync — one memory. There is no mandatory central server: a node exports
an encrypted bundle of its ledger, another node imports and merges it.

CRDT model: the why-memory is a grow-only set keyed by record id, so merging two
nodes is an idempotent union. Each node chains its own records, so a merged
ledger stays individually verifiable (see `memory.verify_chain`).

Cryptography: bundles use **AES-256-GCM** (a vetted AEAD) when the optional
`cryptography` package is installed — `pip install autarch[crypto]`. To preserve
zero-dependency installs, it transparently falls back to a stdlib-only
encrypt-then-MAC construction (HMAC-SHA256 keystream) when that package is absent.
The on-wire format is self-describing (a scheme tag), so AES-GCM and the fallback
interoperate and the cipher can be upgraded without breaking existing bundles. A
node must have `cryptography` installed to *read* an AES-GCM bundle.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .memory import WhyMemory, _seal
from .policy import Policy

_BUNDLE_VERSION = 1

# Cipher wire format: magic + 1-byte scheme tag + scheme-specific body.
_CIPHER_MAGIC = b"SVRN"
_SCHEME_HMAC = 1     # stdlib fallback: nonce(16) + tag(32) + ciphertext
_SCHEME_AESGCM = 2   # AES-256-GCM: nonce(12) + ciphertext(+16-byte tag)
_HMAC_NONCE_BYTES = 16
_GCM_NONCE_BYTES = 12


def _aesgcm_available() -> bool:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Identity / realm
# --------------------------------------------------------------------------- #
@dataclass
class Realm:
    """A shared Autarch identity. Every node holding `key` belongs to it."""

    name: str
    node_id: str
    key: bytes
    policies: List[dict] = field(default_factory=list)
    peers: List[str] = field(default_factory=list)

    @classmethod
    def create(cls, name: str, policies: Optional[List[dict]] = None) -> "Realm":
        return cls(
            name=name,
            node_id="node_" + secrets.token_hex(6),
            key=secrets.token_bytes(32),
            policies=policies or [],
        )

    @classmethod
    def join(cls, name: str, key_hex: str, policies: Optional[List[dict]] = None) -> "Realm":
        """Join an existing realm by its shared key (as hex), as a *new* node."""
        return cls(
            name=name,
            node_id="node_" + secrets.token_hex(6),
            key=bytes.fromhex(key_hex),
            policies=policies or [],
        )

    @property
    def key_hex(self) -> str:
        return self.key.hex()

    def add_peer(self, url: str) -> bool:
        """Register a peer URL for gossip. Returns True if newly added."""
        url = url.rstrip("/")
        if url and url not in self.peers:
            self.peers.append(url)
            return True
        return False

    def remove_peer(self, url: str) -> bool:
        url = url.rstrip("/")
        if url in self.peers:
            self.peers.remove(url)
            return True
        return False

    def realm_policies(self) -> List[Policy]:
        """Reconstruct shared (serializable) policies into Policy objects.

        Mesh-shared policies are capability-level (name/effect/capability/reason);
        predicate-based policies stay local since functions are not portable.
        """
        out: List[Policy] = []
        for spec in self.policies:
            out.append(
                Policy(
                    name=spec["name"],
                    effect=spec["effect"],
                    capability=spec.get("capability", "*"),
                    reason=spec.get("reason", ""),
                )
            )
        return out

    # -- persistence ------------------------------------------------------
    def save(self, workspace) -> Path:
        path = Path(workspace) / ".autarch" / "realm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "realm": self.name,
                    "node_id": self.node_id,
                    "key": self.key_hex,
                    "policies": self.policies,
                    "peers": self.peers,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, workspace) -> Optional["Realm"]:
        path = Path(workspace) / ".autarch" / "realm.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=data["realm"],
            node_id=data["node_id"],
            key=bytes.fromhex(data["key"]),
            policies=data.get("policies", []),
            peers=data.get("peers", []),
        )


# --------------------------------------------------------------------------- #
# Authenticated encryption (AES-256-GCM, with a stdlib fallback)
# --------------------------------------------------------------------------- #
class Cipher:
    """Authenticated encryption for mesh bundles.

    Prefers **AES-256-GCM** (a vetted AEAD) when the ``cryptography`` package is
    installed; otherwise falls back to a stdlib-only encrypt-then-MAC
    construction (HMAC-SHA256 keystream). Blobs are self-describing via a scheme
    tag, so the cipher can be upgraded transparently. Pass ``prefer`` to force a
    scheme (mainly for tests).
    """

    def __init__(self, key: bytes, prefer: Optional[str] = None):
        if len(key) not in (16, 24, 32):
            raise ValueError("realm key must be 16, 24, or 32 bytes")
        self._key = key
        # Derived subkeys for the HMAC fallback (separate enc/mac domains).
        self._enc = hmac.new(key, b"autarch-enc-v1", hashlib.sha256).digest()
        self._mac = hmac.new(key, b"autarch-mac-v1", hashlib.sha256).digest()

        if prefer == "hmac":
            self._use_aesgcm = False
        elif prefer == "aesgcm":
            if not _aesgcm_available():
                raise RuntimeError("AES-GCM requested but `cryptography` is not installed")
            self._use_aesgcm = True
        else:
            self._use_aesgcm = _aesgcm_available()

    @property
    def scheme(self) -> str:
        return "aesgcm" if self._use_aesgcm else "hmac"

    # -- public API -------------------------------------------------------
    def encrypt(self, plaintext: bytes) -> bytes:
        if self._use_aesgcm:
            return self._encrypt_aesgcm(plaintext)
        return self._encrypt_hmac(plaintext)

    def decrypt(self, blob: bytes) -> bytes:
        if blob[:4] == _CIPHER_MAGIC:
            scheme = blob[4]
            body = blob[5:]
            if scheme == _SCHEME_AESGCM:
                return self._decrypt_aesgcm(body)
            if scheme == _SCHEME_HMAC:
                return self._decrypt_hmac(body)
            raise ValueError(f"unknown cipher scheme {scheme}")
        # A blob without the magic header is a legacy raw HMAC bundle.
        return self._decrypt_hmac(blob)

    # -- AES-256-GCM ------------------------------------------------------
    def _encrypt_aesgcm(self, plaintext: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(_GCM_NONCE_BYTES)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext, None)
        return _CIPHER_MAGIC + bytes([_SCHEME_AESGCM]) + nonce + ciphertext

    def _decrypt_aesgcm(self, body: bytes) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.exceptions import InvalidTag
        except Exception as exc:  # the producer had cryptography; this reader doesn't
            raise RuntimeError(
                "this bundle is AES-GCM encrypted; install `cryptography` to read it "
                "(pip install autarch[crypto])"
            ) from exc

        nonce = body[:_GCM_NONCE_BYTES]
        ciphertext = body[_GCM_NONCE_BYTES:]
        try:
            return AESGCM(self._key).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise ValueError("authentication failed: wrong realm key or tampered bundle") from exc

    # -- HMAC keystream (stdlib fallback) ---------------------------------
    def _keystream(self, nonce: bytes, length: int) -> bytes:
        blocks = bytearray()
        counter = 0
        while len(blocks) < length:
            blocks += hmac.new(
                self._enc, nonce + counter.to_bytes(8, "big"), hashlib.sha256
            ).digest()
            counter += 1
        return bytes(blocks[:length])

    def _encrypt_hmac(self, plaintext: bytes) -> bytes:
        nonce = secrets.token_bytes(_HMAC_NONCE_BYTES)
        keystream = self._keystream(nonce, len(plaintext))
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, keystream))
        tag = hmac.new(self._mac, nonce + ciphertext, hashlib.sha256).digest()
        return _CIPHER_MAGIC + bytes([_SCHEME_HMAC]) + nonce + tag + ciphertext

    def _decrypt_hmac(self, body: bytes) -> bytes:
        nonce = body[:_HMAC_NONCE_BYTES]
        tag = body[_HMAC_NONCE_BYTES:_HMAC_NONCE_BYTES + 32]
        ciphertext = body[_HMAC_NONCE_BYTES + 32:]
        expected = hmac.new(self._mac, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, tag):
            raise ValueError("authentication failed: wrong realm key or tampered bundle")
        keystream = self._keystream(nonce, len(ciphertext))
        return bytes(a ^ b for a, b in zip(ciphertext, keystream))


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #
@dataclass
class MergeReport:
    added: int = 0
    skipped: int = 0
    rejected: int = 0
    policies_added: int = 0
    from_node: str = ""
    rejected_ids: List[str] = field(default_factory=list)

    def summary(self) -> str:
        msg = f"merged from {self.from_node or 'peer'}: +{self.added} new, {self.skipped} already known"
        if self.policies_added:
            msg += f", +{self.policies_added} shared policy(ies)"
        if self.rejected:
            msg += f", {self.rejected} REJECTED (failed integrity)"
        return msg


def export_bundle(memory: WhyMemory, realm: Realm) -> bytes:
    """Produce an encrypted, authenticated bundle of this node's ledger.

    The bundle also carries the realm's shared policy set, so policies defined on
    one node converge across the mesh as nodes sync.
    """
    bundle = {
        "version": _BUNDLE_VERSION,
        "realm": realm.name,
        "from_node": realm.node_id,
        "policies": realm.policies,
        "records": memory.export_rows(),
    }
    plaintext = json.dumps(bundle).encode("utf-8")
    return Cipher(realm.key).encrypt(plaintext)


def _merge_policies(realm: Realm, incoming: List[dict]) -> int:
    """Union shared policies into the realm by name (monotonic — only adds)."""
    have = {p.get("name") for p in realm.policies}
    added = 0
    for spec in incoming:
        if spec.get("name") not in have:
            realm.policies.append(spec)
            have.add(spec.get("name"))
            added += 1
    return added


def import_bundle(memory: WhyMemory, realm: Realm, blob: bytes) -> MergeReport:
    """Decrypt, authenticate, and merge a peer's bundle (union by id).

    Each incoming record is re-checked against its own seal before acceptance, so
    a peer cannot inject a forged or altered record even though it holds the
    realm key. Shared policies in the bundle are unioned into `realm` (the caller
    persists the realm if it wants the change to survive).
    """
    plaintext = Cipher(realm.key).decrypt(blob)  # raises on auth failure
    bundle = json.loads(plaintext.decode("utf-8"))

    if bundle.get("realm") != realm.name:
        raise ValueError(
            f"realm mismatch: bundle is for '{bundle.get('realm')}', this node is '{realm.name}'"
        )

    report = MergeReport(from_node=bundle.get("from_node", ""))
    for row in bundle.get("records", []):
        if memory.has(row["id"]):
            report.skipped += 1
            continue
        seal = row.get("seal")
        # Accept unsealed (legacy) rows as-is; verify sealed rows before trusting.
        if seal is not None and _seal(row.get("prev_seal") or "", row["payload"]) != seal:
            report.rejected += 1
            report.rejected_ids.append(row["id"])
            continue
        if memory.import_row(row):
            report.added += 1

    report.policies_added = _merge_policies(realm, bundle.get("policies", []))
    return report
