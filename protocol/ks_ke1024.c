/*
 * SHA-KE-1024 -- 1024-bit integrity digest used by KeShare 1.0.
 *
 *   state   16 lanes x 64 bit (1024 bit)
 *   block   128 bytes (16 words), same width as the state
 *   round   BLAKE2b-style G, rotation 32/24/16/63, 16 rounds,
 *           message words chosen by the standard BLAKE2 sigma table
 *   feed    h[i] ^= v[i] ^ v[(i + 8) % 16]
 *   pad     0x80, zeros, 128-bit big-endian bit length (multiple of 128 bytes)
 *   output  h[0..15] big-endian, 128 bytes / 256 hex chars
 *
 * 64-bit integer arithmetic only: no floating point, no int128, no libc
 * dependency beyond memcpy/memset.  The Python reference in
 * protocol/sha_ke_1024.py is byte-identical (vectors in test/ke_vectors.h).
 */
#include "ks_ke1024.h"

#include <string.h>

#define KE_ROTR64(x, n) (((x) >> (n)) | ((x) << (64 - (n))))
#define KE_ALLONES      0xffffffffffffffffULL

static const uint64_t KE_IV[16] = {
    0x428a2f98d728ae22ULL, 0x7137449123ef65cdULL,
    0xb5c0fbcfec4d3b2fULL, 0xe9b5dba58189dbbcULL,
    0x3956c25bf348b538ULL, 0x59f111f1b605d019ULL,
    0x923f82a4af194f9bULL, 0xab1c5ed5da6d8118ULL,
    0xd807aa98a3030242ULL, 0x12835b0145706fbeULL,
    0x243185be4ee4b28cULL, 0x550c7dc3d5ffb4e2ULL,
    0x72be5d74f27b896fULL, 0x80deb1fe3b1696b1ULL,
    0x9bdc06a725c71235ULL, 0xc19bf174cf692694ULL
};

static const uint8_t KE_SIGMA[16][16] = {
    {  0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15 },
    { 14, 10,  4,  8,  9, 15, 13,  6,  1, 12,  0,  2, 11,  7,  5,  3 },
    { 11,  8, 12,  0,  5,  2, 15, 13, 10, 14,  3,  6,  7,  1,  9,  4 },
    {  7,  9,  3,  1, 13, 12, 11, 14,  2,  6,  5, 10,  4,  0, 15,  8 },
    {  9,  0,  5,  7,  2,  4, 10, 15, 14,  1, 11, 12,  6,  8,  3, 13 },
    {  2, 12,  6, 10,  0, 11,  8,  3,  4, 13,  7,  5, 15, 14,  1,  9 },
    { 12,  5,  1, 15, 14, 13,  4, 10,  0,  7,  6,  3,  9,  2,  8, 11 },
    { 13, 11,  7, 14, 12,  1,  3,  9,  5,  0, 15,  4,  8,  6,  2, 10 },
    {  6, 15, 14,  9, 11,  3,  0,  8, 12,  2, 13,  7,  1,  4, 10,  5 },
    { 10,  2,  8,  4,  7,  6,  1,  5, 15, 11,  9, 14,  3, 12, 13,  0 },
    {  0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15 },
    { 14, 10,  4,  8,  9, 15, 13,  6,  1, 12,  0,  2, 11,  7,  5,  3 },
    { 11,  8, 12,  0,  5,  2, 15, 13, 10, 14,  3,  6,  7,  1,  9,  4 },
    {  7,  9,  3,  1, 13, 12, 11, 14,  2,  6,  5, 10,  4,  0, 15,  8 },
    {  9,  0,  5,  7,  2,  4, 10, 15, 14,  1, 11, 12,  6,  8,  3, 13 },
    {  2, 12,  6, 10,  0, 11,  8,  3,  4, 13,  7,  5, 15, 14,  1,  9 }
};

static uint64_t ke_ld64be(const uint8_t *p)
{
    uint64_t v = 0;
    int i;
    for (i = 0; i < 8; i++) v = (v << 8) | p[i];
    return v;
}

static void ke_st64be(uint8_t *p, uint64_t v)
{
    int i;
    for (i = 7; i >= 0; i--) { p[i] = (uint8_t)v; v >>= 8; }
}

static void ke_g(uint64_t v[16], int a, int b, int c, int d, uint64_t x, uint64_t y)
{
    v[a] = v[a] + v[b] + x;
    v[d] = KE_ROTR64(v[d] ^ v[a], 32);
    v[c] = v[c] + v[d];
    v[b] = KE_ROTR64(v[b] ^ v[c], 24);
    v[a] = v[a] + v[b] + y;
    v[d] = KE_ROTR64(v[d] ^ v[a], 16);
    v[c] = v[c] + v[d];
    v[b] = KE_ROTR64(v[b] ^ v[c], 63);
}

static void ke_compress(uint64_t h[16], const uint8_t block[128],
                        uint64_t counter, int last)
{
    uint64_t m[16], v[16];
    int i, r;

    for (i = 0; i < 16; i++) m[i] = ke_ld64be(block + 8 * i);
    for (i = 0; i < 16; i++) v[i] = h[i];
    v[12] ^= counter;
    v[13] ^= 0;
    if (last) {
        v[14] ^= KE_ALLONES;
        v[15] ^= KE_ALLONES;
    }

    for (r = 0; r < 16; r++) {
        const uint8_t *s = KE_SIGMA[r];
        ke_g(v, 0, 4,  8, 12, m[s[0]],  m[s[1]]);
        ke_g(v, 1, 5,  9, 13, m[s[2]],  m[s[3]]);
        ke_g(v, 2, 6, 10, 14, m[s[4]],  m[s[5]]);
        ke_g(v, 3, 7, 11, 15, m[s[6]],  m[s[7]]);
        ke_g(v, 0, 5, 10, 15, m[s[8]],  m[s[9]]);
        ke_g(v, 1, 6, 11, 12, m[s[10]], m[s[11]]);
        ke_g(v, 2, 7,  8, 13, m[s[12]], m[s[13]]);
        ke_g(v, 3, 4,  9, 14, m[s[14]], m[s[15]]);
    }

    for (i = 0; i < 16; i++) h[i] ^= v[i] ^ v[(i + 8) & 15];
}

void ke_sha1024_init(ke_sha1024_ctx *ctx)
{
    int i;
    for (i = 0; i < 16; i++) ctx->h[i] = KE_IV[i];
    ctx->total = 0;
    ctx->processed = 0;
    ctx->buflen = 0;
}

void ke_sha1024_update(ke_sha1024_ctx *ctx, const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;

    if (len == 0) return;
    ctx->total += (uint64_t)len;

    /* the last full block stays buffered so that it can carry the final flag */
    if (ctx->buflen == 128) {
        ke_compress(ctx->h, ctx->buf, ctx->processed + 128, 0);
        ctx->processed += 128;
        ctx->buflen = 0;
    }

    if (ctx->buflen) {
        size_t need = 128 - ctx->buflen;
        size_t take = len < need ? len : need;
        memcpy(ctx->buf + ctx->buflen, p, take);
        ctx->buflen += (uint32_t)take;
        p += take;
        len -= take;
    }
    while (len >= 128) {
        if (len == 128) break;          /* keep the last full block buffered */
        ke_compress(ctx->h, p, ctx->processed + 128, 0);
        ctx->processed += 128;
        p += 128;
        len -= 128;
    }
    if (len) {
        memcpy(ctx->buf, p, len);
        ctx->buflen = (uint32_t)len;
    }
}

void ke_sha1024_final(ke_sha1024_ctx *ctx, uint8_t out[128])
{
    uint64_t bits = ctx->total << 3;
    uint64_t highBits = ctx->total >> 61;
    uint8_t block[256];
    uint32_t total;
    uint32_t off;
    int i;

    memset(block, 0, sizeof block);
    memcpy(block, ctx->buf, ctx->buflen);
    total = ctx->buflen;
    block[total++] = 0x80;
    while ((total % 128) != 112) block[total++] = 0;
    if (total + 16 > sizeof block) total = 240;   /* cannot happen with 128-byte buf */
    ke_st64be(block + total, highBits);
    ke_st64be(block + total + 8, bits);
    total += 16;

    for (off = 0; off < total; off += 128) {
        int last = (off + 128) >= total;
        ke_compress(ctx->h, block + off, ctx->processed + off + 128, last);
    }

    for (i = 0; i < 16; i++) ke_st64be(out + 8 * i, ctx->h[i]);
    memset(ctx, 0, sizeof *ctx);
}

void ke_sha1024(const void *data, size_t len, uint8_t out[128])
{
    ke_sha1024_ctx ctx;
    ke_sha1024_init(&ctx);
    ke_sha1024_update(&ctx, data, len);
    ke_sha1024_final(&ctx, out);
}

void ke_sha1024_hex(const void *data, size_t len, char out[KE_SHA1024_HEX_LEN])
{
    static const char H[] = "0123456789abcdef";
    uint8_t d[128];
    int i;
    ke_sha1024(data, len, d);
    for (i = 0; i < 128; i++) {
        out[2 * i] = H[d[i] >> 4];
        out[2 * i + 1] = H[d[i] & 15];
    }
    out[256] = 0;
}
