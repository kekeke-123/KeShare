# -*- coding: utf-8 -*-
"""KeShare hashing helpers: SHA-256 (session token) + SHA-KE-1024 (files).

Two algorithms are used, for two different purposes:

* **SHA-256** -- only for the CTL session token
  (``PROTOCOL.md`` section 2)::

      token = hex(SHA-256("KS1-CTL\\0" + did_A + nonce_A + did_B + nonce_B))[:16]

* **SHA-KE-1024** -- every file digest on the wire (128 byte / 256 hex
  characters).  Implemented in :mod:`keshare.ke1024`; the helpers are
  re-exported here so the rest of the code imports one module:
  :func:`sha_ke_1024`, :func:`sha_ke_1024_hex`, :func:`sha_ke_1024_file`.

No encryption, no key exchange: KeShare 1.0 only checks integrity.
"""

import hashlib
import os

from .ke1024 import Ke1024, sha_ke_1024, sha_ke_1024_file, sha_ke_1024_hex

__all__ = [
    "sha256", "sha256_hex", "token_for",
    "Ke1024", "sha_ke_1024", "sha_ke_1024_hex", "sha_ke_1024_file",
    "hexb", "unhex", "ct_equal", "rand_bytes", "rand_hex", "CTL_LABEL",
]

CTL_LABEL = b"KS1-CTL\0"


# ---------------------------------------------------------------------------
# SHA-256 (token derivation only)
# ---------------------------------------------------------------------------
def sha256(data):
    """Raw 32 byte SHA-256 digest of *data*."""
    return hashlib.sha256(data).digest()


def sha256_hex(data):
    """Lowercase hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def token_for(did_a, nonce_a, did_b, nonce_b):
    """Hex token binding a CTL session to the DATA channel.

    ``token = hex(SHA-256("KS1-CTL\\0" + did_A + nonce_A + did_B + nonce_B))[:16]``

    A is the side that sent ``hello`` (the initiator), B is the side that
    answered with ``hello_ack``.  All four inputs are ASCII strings exactly as
    they appear on the wire.
    """
    parts = [CTL_LABEL]
    for item in (did_a, nonce_a, did_b, nonce_b):
        if item is None:
            item = ""
        if not isinstance(item, bytes):
            item = str(item).encode("ascii", "ignore")
        parts.append(item)
    return hexb(sha256(b"".join(parts)))[:16]


# ---------------------------------------------------------------------------
# Encoding / randomness helpers
# ---------------------------------------------------------------------------
def hexb(data):
    return "".join("%02x" % b for b in bytearray(data))


def unhex(text):
    if not isinstance(text, str):
        raise ValueError("hex value must be a string")
    text = text.strip()
    if len(text) % 2:
        raise ValueError("hex value has odd length")
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise ValueError("invalid hex value")


def ct_equal(a, b):
    """Constant time comparison (used for token/digest comparison)."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(bytearray(a), bytearray(b)):
        diff |= x ^ y
    return diff == 0


def rand_bytes(n):
    return os.urandom(n)


def rand_hex(nbytes):
    return hexb(os.urandom(nbytes))
