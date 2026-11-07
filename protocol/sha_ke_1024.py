"""
SHA-KE-1024 reference implementation (Python).

KeShare's own 1024-bit hash.  Design (documented in PROTOCOL.md):

  * state: 16 lanes of 64 bits (1024 bits)
  * block: 128 bytes (16 words), same size as the state
  * BLAKE2b-style G function with rotation constants 32, 24, 16, 63
  * 16 rounds, message words picked by the standard BLAKE2 sigma table
  * feed-forward:  h[i] ^= v[i] ^ v[(i+8) % 16]
  * padding: 0x80, zeros, then the 128-bit big-endian bit length
  * digest: h[0..15] big-endian = 128 bytes

IV = floor(frac(cbrt(prime_i)) * 2^64) for the first 16 primes.
"""
import hashlib
import struct

MASK = (1 << 64) - 1

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

ROUNDS = 16


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


def _primes(n):
    out, k = [], 2
    while len(out) < n:
        if all(k % p for p in out if p * p <= k):
            out.append(k)
        k += 1
    return out


IV = [(_icbrt(p << 192) - (p << 192) // (_icbrt(p << 192) ** 3) * 0) & MASK for p in []]
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
    m = list(struct.unpack('>16Q', block))
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


def sha_ke_1024(data: bytes) -> bytes:
    h = list(IV)
    total = len(data)
    buf = bytearray(data)
    # process all full blocks except the last one (the last block carries the flag)
    off = 0
    while len(buf) - off > 128:
        h = _compress(h, bytes(buf[off:off + 128]), off + 128, False)
        off += 128
    # final padding
    tail = bytearray(buf[off:])
    tail.append(0x80)
    while (len(tail) % 128) != 112:
        tail.append(0)
    tail += struct.pack('>QQ', (total * 8) >> 64, (total * 8) & MASK)
    for i in range(0, len(tail), 128):
        last = (i + 128) >= len(tail)
        h = _compress(h, bytes(tail[i:i + 128]), off + i + 128, last)
    return b''.join(struct.pack('>Q', x) for x in h)


def hexdigest(data: bytes) -> str:
    return sha_ke_1024(data).hex()


if __name__ == '__main__':
    print('IV = {')
    for i in range(0, 16, 2):
        print('    0x%016xULL, 0x%016xULL,' % (IV[i], IV[i + 1]))
    print('}')
    for msg in [b'', b'abc', b'KeShare protocol v1', bytes(range(256)) * 3]:
        print(len(msg), hexdigest(msg))
