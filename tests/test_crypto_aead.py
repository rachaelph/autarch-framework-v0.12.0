"""AEAD crypto tests — AES-256-GCM with a stdlib fallback, interoperable.

The fallback (HMAC keystream) tests always run. The AES-GCM tests skip cleanly
when `cryptography` is not installed, so the suite is green on any platform while
still proving the AEAD path wherever the library is available.
"""
import pytest

from autarch.mesh import Cipher, _aesgcm_available

KEY = b"k" * 32


# --- stdlib fallback (always available) -----------------------------------

def test_fallback_roundtrip():
    c = Cipher(KEY, prefer="hmac")
    assert c.scheme == "hmac"
    blob = c.encrypt(b"hello")
    assert c.decrypt(blob) == b"hello"


def test_fallback_wrong_key_fails():
    blob = Cipher(b"a" * 32, prefer="hmac").encrypt(b"secret")
    with pytest.raises(ValueError):
        Cipher(b"b" * 32, prefer="hmac").decrypt(blob)


def test_fallback_tamper_detected():
    c = Cipher(KEY, prefer="hmac")
    blob = bytearray(c.encrypt(b"secret"))
    blob[-1] ^= 0x01
    with pytest.raises(ValueError):
        c.decrypt(bytes(blob))


def test_blob_is_self_describing():
    blob = Cipher(KEY, prefer="hmac").encrypt(b"x")
    assert blob[:4] == b"SVRN"
    assert blob[4] == 1  # scheme tag: HMAC


def test_legacy_headerless_blob_decrypts():
    # A blob without the magic header is treated as a legacy raw HMAC bundle.
    c = Cipher(KEY, prefer="hmac")
    full = c.encrypt(b"legacy payload")
    headerless = full[5:]  # strip magic(4) + scheme(1)
    assert c.decrypt(headerless) == b"legacy payload"


def test_key_length_validated():
    with pytest.raises(ValueError):
        Cipher(b"too short")


# --- AES-256-GCM (only when `cryptography` is installed) ------------------

def test_aesgcm_roundtrip():
    pytest.importorskip("cryptography")
    c = Cipher(KEY, prefer="aesgcm")
    assert c.scheme == "aesgcm"
    blob = c.encrypt(b"hello aead")
    assert blob[:4] == b"SVRN" and blob[4] == 2  # scheme tag: AES-GCM
    assert c.decrypt(blob) == b"hello aead"


def test_aesgcm_tamper_detected():
    pytest.importorskip("cryptography")
    c = Cipher(KEY, prefer="aesgcm")
    blob = bytearray(c.encrypt(b"secret"))
    blob[-1] ^= 0x01
    with pytest.raises(ValueError):
        c.decrypt(bytes(blob))


def test_aesgcm_wrong_key_fails():
    pytest.importorskip("cryptography")
    blob = Cipher(b"a" * 32, prefer="aesgcm").encrypt(b"secret")
    with pytest.raises(ValueError):
        Cipher(b"b" * 32, prefer="aesgcm").decrypt(blob)


def test_default_prefers_aesgcm_when_available():
    # The default scheme is AES-GCM if the library is present, else the fallback.
    expected = "aesgcm" if _aesgcm_available() else "hmac"
    assert Cipher(KEY).scheme == expected
