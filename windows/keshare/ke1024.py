# -*- coding: utf-8 -*-
"""SHA-KE-1024 -- KeShare's 1024-bit file digest.

Ported from the reference implementation ``protocol/sha_ke_1024.py`` (must stay
byte-identical):

  * state: 16 lanes of 64 bits (1024 bits)
  * block: 128 bytes (16 words), same size as the state
  * BLAKE2b-style G function, rotation constants 32, 24, 16, 63
  * 16 rounds, message words picked by the standard BLAKE2 sigma table
  * counter (bytes processed so far, including the current block) XORed into
    ``v[12]`` (``v[13]`` is the always-zero high half)
  * on the final block only: ``v[14] ^= v[15] ^= 0xFFFFFFFFFFFFFFFF``
  * feed-forward: ``h[i] ^= v[i] ^ v[(i + 8) % 16]``
  * padding: ``0x80``, zeros so that ``len % 128 == 112``, then a 128-bit
    big-endian **bit** length
  * digest: ``h[0..15]`` big-endian -> 128 bytes -> 256 lowercase hex chars

The IV is ``floor(frac(cbrt(prime_i)) * 2**64)`` for the first 16 primes, which
:data:`IV` computes with integer maths (no floating point).

On top of the one-shot reference API this module adds an incremental hasher
(:class:`Ke1024`) so files can be hashed while they are being written; its
output is identical to :func:`sha_ke_1024` for every input (covered by tests
and by ``if __name__ == "__main__"``).
"""

import struct

MASK = (1 << 64) - 1
BLOCK = 128
ROUNDS = 16

SIGMA = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
    [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
    [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
    [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
    [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
]


def _icbrt(n):
    x = 1 << ((n.bit_length() + 2) // 3)
    while True:
        y = (2 * x + n // (x * x)) // 3
        if y >= x:
            break
        x = y
    while (x + 1) ** 3 <= n:
        x += 1
    while x ** 3 > n:
        x -= 1
    return x


def _primes(count):
    out, k = [], 2
    while len(out) < count:
        if all(k % p for p in out if p * p <= k):
            out.append(k)
        k += 1
    return out


# floor(frac(cbrt(p)) * 2**64) for the first 16 primes
IV = [(_icbrt(p << 192)) & MASK for p in _primes(16)]


def _ror(x, n):
    return ((x >> n) | (x << (64 - n))) & MASK


def _g(v, a, b, c, d, x, y):
    v[a] = (v[a] + v[b] + x) & MASK
    v[d] = _ror(v[d] ^ v[a], 32)
    v[c] = (v[c] + v[d]) & MASK
    v[b] = _ror(v[b] ^ v[c], 24)
    v[a] = (v[a] + v[b] + y) & MASK
    v[d] = _ror(v[d] ^ v[a], 16)
    v[c] = (v[c] + v[d]) & MASK
    v[b] = _ror(v[b] ^ v[c], 63)


def _compress(h, block, counter, last):
    m = list(struct.unpack(">16Q", block))
    v = list(h)
    v[12] ^= counter & MASK
    v[13] ^= (counter >> 64) & MASK
    if last:
        v[14] ^= MASK
        v[15] ^= MASK
    for r in range(ROUNDS):
        s = SIGMA[r % len(SIGMA)]
        _g(v, 0, 4, 8, 12, m[s[0]], m[s[1]])
        _g(v, 1, 5, 9, 13, m[s[2]], m[s[3]])
        _g(v, 2, 6, 10, 14, m[s[4]], m[s[5]])
        _g(v, 3, 7, 11, 15, m[s[6]], m[s[7]])
        _g(v, 0, 5, 10, 15, m[s[8]], m[s[9]])
        _g(v, 1, 6, 11, 12, m[s[10]], m[s[11]])
        _g(v, 2, 7, 8, 13, m[s[12]], m[s[13]])
        _g(v, 3, 4, 9, 14, m[s[14]], m[s[15]])
    for i in range(16):
        h[i] ^= v[i] ^ v[(i + 8) % 16]
    return h


class Ke1024(object):
    """Incremental SHA-KE-1024 hasher (same output as :func:`sha_ke_1024`)."""

    __slots__ = ("_h", "_buf", "_counter", "count")

    def __init__(self):
        self._h = list(IV)
        self._buf = bytearray()
        self._counter = 0          # bytes compressed so far
        self.count = 0             # total bytes fed

    def update(self, data):
        if not data:
            return self
        if not isinstance(data, (bytes, bytearray, memoryview)):
            data = bytes(data)
        self.count += len(data)
        self._buf += data
        while len(self._buf) > BLOCK:
            block = bytes(self._buf[:BLOCK])
            del self._buf[:BLOCK]
            self._counter += BLOCK
            self._h = _compress(self._h, block, self._counter, False)
        return self

    def digest(self):
        """Return the 128 byte digest (does not consume the hasher state)."""
        h = list(self._h)
        tail = bytearray(self._buf)
        tail.append(0x80)
        while (len(tail) % BLOCK) != 112:
            tail.append(0)
        bits = self.count * 8
        tail += struct.pack(">QQ", (bits >> 64) & MASK, bits & MASK)
        counter = self._counter
        for offset in range(0, len(tail), BLOCK):
            counter += BLOCK
            last = (offset + BLOCK) >= len(tail)
            h = _compress(h, bytes(tail[offset:offset + BLOCK]), counter, last)
        return b"".join(struct.pack(">Q", x) for x in h)

    def hexdigest(self):
        """Return the 256 character lowercase hex digest."""
        return "".join("%02x" % b for b in bytearray(self.digest()))

    def copy(self):
        other = Ke1024()
        other._h = list(self._h)
        other._buf = bytearray(self._buf)
        other._counter = self._counter
        other.count = self.count
        return other


def sha_ke_1024(data):
    """One-shot 128 byte digest of *data*."""
    return Ke1024().update(data).digest()


def sha_ke_1024_hex(data):
    """One-shot 256 character lowercase hex digest of *data*."""
    return Ke1024().update(data).hexdigest()


def sha_ke_1024_file(path, chunk=256 * 1024):
    """256 character lowercase hex digest of a file on disk."""
    hasher = Ke1024()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


VECTORS = [
    (b"",
     "c4c3cc4ab16aff9d0ac4802468fd7dfac2785133349d14a4a4b74b3bb3c591a3"
     "1865db44b11337444fb97f6ce9f19f9ddad0428d3981d23841f2e130acf1dd5e"
     "5e4e494ac54153fd69709fb40e62778953892f4296349d07180eed5de7b3fefd"
     "538d446bb0200b139696df6364e2d935d333c68eb35f8f962b754e91b9f57ad2"),
    (b"abc",
     "19e537f5fabe26188455cfc2c5aadca000122fbeb2fa4dd6a9145f790871a24a"
     "68b2035e1f84f158d7696ff5b4a7baad58920e254d00fce54b42ed6749cbe3ae"
     "8368b2f58e958a78e7e1d052a335d6d391e351cf1053c47515adf91f5c07cd14"
     "235a9c711eb7cd0f0e46cffa39b4fc0551718a26c7dea14b21c542c65ccf4422"),
    (b"KeShare protocol v1",
     "05d719ae6e3682fe3c7df34628f804f29d1d6198bd3d4dcea94ef08a688602e7"
     "1da8318d3453895c3783073318179cc76fce7d67e2b096f8843263a000f70a3b"
     "9f5a9cae1a1d2e9e5fc9ecd64e670e810cec1fe91f94c46d15f756ec3cf06db9"
     "5640aea23560b50beeaca73c9504da6f662df964686ecb56eeb5cc0115f3adb7"),
    (bytes(range(256)) * 3,
     "0438a990feafb49147b4bc5d1546cc5ca405da5d57f462f1d5e4209d50e636e"
     "5e0e1658f87696e2d509a10083423aa40930854cc8185e5376ba0a66df71c709"
     "99eb52c908a8418f12400a3cd73d9c62f35f4a42cf55deb52695d86fb049059b"
     "bab09faa0865a527a89b5b007b930ece89aebd0cf0b5bb899012709cce218d715"),
]


def selftest():
    """Check the published vectors and streaming/one-shot equivalence."""
    for data, expected in VECTORS:
        got = sha_ke_1024_hex(data)
        if got != expected:
            raise AssertionError(
                "vector mismatch for %d bytes: %s != %s" % (len(data), got, expected))
        if sha_ke_1024(data) != bytes.fromhex(expected):
            raise AssertionError("raw digest mismatch for %d bytes" % len(data))
        for size in (1, 3, 127, 128, 129, 1000):
            hasher = Ke1024()
            for index in range(0, len(data), size):
                hasher.update(data[index:index + size])
            if hasher.hexdigest() != expected:
                raise AssertionError("streaming mismatch (chunk %d)" % size)
    return True


if __name__ == "__main__":
    selftest()
    print("OK: SHA-KE-1024 vectors pass (%d vectors)" % len(VECTORS))
