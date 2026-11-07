/*
 * SHA-KE-1024 -- 1024-bit integrity digest for KeShare.
 * See ks_ke1024.c for the specification.
 */
#ifndef KS_KE1024_H
#define KS_KE1024_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define KE_SHA1024_BYTES    128
#define KE_SHA1024_HEX_LEN  257      /* 256 hex chars + NUL */

typedef struct {
    uint64_t h[16];
    uint64_t total;       /* total bytes fed */
    uint64_t processed;   /* bytes already compressed */
    uint8_t  buf[128];
    uint32_t buflen;
} ke_sha1024_ctx;

void ke_sha1024_init(ke_sha1024_ctx *ctx);
void ke_sha1024_update(ke_sha1024_ctx *ctx, const void *data, size_t len);
void ke_sha1024_final(ke_sha1024_ctx *ctx, uint8_t out[KE_SHA1024_BYTES]);
void ke_sha1024(const void *data, size_t len, uint8_t out[KE_SHA1024_BYTES]);
void ke_sha1024_hex(const void *data, size_t len, char out[KE_SHA1024_HEX_LEN]);

#ifdef __cplusplus
}
#endif
#endif
