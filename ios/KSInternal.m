//
//  KSInternal.m -- 内部共享实现
//

#import "KSInternal.h"
#import <CommonCrypto/CommonDigest.h>
#import <arpa/inet.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <sys/socket.h>
#import <unistd.h>
#import <ifaddrs.h>

BOOL KSReadFull(int fd, void *buf, size_t len)
{
    uint8_t *p = (uint8_t *)buf;
    while (len > 0) {
        ssize_t n = read(fd, p, len);
        if (n <= 0) return NO;
        p += n;
        len -= (size_t)n;
    }
    return YES;
}

void KSWriteFull(int fd, const void *buf, size_t len)
{
    const uint8_t *p = (const uint8_t *)buf;
    while (len > 0) {
        ssize_t n = write(fd, p, len);
        if (n <= 0) return;
        p += n;
        len -= (size_t)n;
    }
}

void KSSetNoDelay(int fd)
{
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
}

NSString *KSIPv4StringLocal(void)
{
    struct ifaddrs *ifa = NULL;
    NSString *out = @"127.0.0.1";
    if (getifaddrs(&ifa) != 0) return out;
    for (struct ifaddrs *p = ifa; p; p = p->ifa_next) {
        if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET) continue;
        char buf[INET_ADDRSTRLEN];
        struct sockaddr_in *sin = (struct sockaddr_in *)p->ifa_addr;
        if (!inet_ntop(AF_INET, &sin->sin_addr, buf, sizeof buf)) continue;
        NSString *s = [NSString stringWithUTF8String:buf];
        if ([s hasPrefix:@"127."] || [s hasPrefix:@"169.254."]) continue;
        // 优先 en0（Wi‑Fi）
        if ([@(p->ifa_name) isEqualToString:@"en0"]) return s;
        if ([out isEqualToString:@"127.0.0.1"]) out = s;
    }
    freeifaddrs(ifa);
    return out;
}

UIColor *KSThemeTextSecondary(void) { return [UIColor colorWithWhite:1.0 alpha:0.62]; }
UIColor *KSSystemAccent(void) { return [UIColor colorWithRed:0.36 green:0.72 blue:1.0 alpha:1.0]; }

NSString *KSSHA256HexOfData(NSData *data)
{
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    char hex[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        static const char *H = "0123456789abcdef";
        hex[2 * i] = H[digest[i] >> 4];
        hex[2 * i + 1] = H[digest[i] & 15];
    }
    hex[CC_SHA256_DIGEST_LENGTH * 2] = 0;
    return [NSString stringWithUTF8String:hex];
}

NSString *KSUniquePathInDirectory(NSString *dir, NSString *name)
{
    NSString *safe = name.length ? name : @"file";
    NSString *candidate = [dir stringByAppendingPathComponent:safe];
    if (![[NSFileManager defaultManager] fileExistsAtPath:candidate]) return candidate;

    NSString *base = [safe stringByDeletingPathExtension];
    NSString *ext = [safe pathExtension];
    for (NSInteger i = 2; i < 1000; i++) {
        NSString *next = ext.length
            ? [NSString stringWithFormat:@"%@ %ld.%@", base, (long)i, ext]
            : [NSString stringWithFormat:@"%@ %ld", base, (long)i];
        candidate = [dir stringByAppendingPathComponent:next];
        if (![[NSFileManager defaultManager] fileExistsAtPath:candidate]) return candidate;
    }
    return [dir stringByAppendingPathComponent:
            [NSString stringWithFormat:@"%@-%@", KSRandomHex(4), safe]];
}

NSString *KSTokenFor(NSString *didA, NSString *nonceA, NSString *didB, NSString *nonceB)
{
    NSMutableData *d = [NSMutableData data];
    [d appendBytes:"KS1-CTL\0" length:8];
    [d appendData:[(didA ?: @"") dataUsingEncoding:NSUTF8StringEncoding]];
    [d appendData:[(nonceA ?: @"") dataUsingEncoding:NSUTF8StringEncoding]];
    [d appendData:[(didB ?: @"") dataUsingEncoding:NSUTF8StringEncoding]];
    [d appendData:[(nonceB ?: @"") dataUsingEncoding:NSUTF8StringEncoding]];
    NSString *hex = KSSHA256HexOfData(d);
    return [hex substringToIndex:16];
}

NSString *KSSHA256HexOfFile(NSString *path, NSError **error)
{
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    NSFileHandle *fh = [NSFileHandle fileHandleForReadingAtPath:path];
    if (!fh) {
        if (error) *error = [NSError errorWithDomain:@"KeShare" code:1
                                            userInfo:@{NSLocalizedDescriptionKey: @"无法读取文件"}];
        return nil;
    }
    while (YES) {
        @autoreleasepool {
            NSData *chunk = [fh readDataOfLength:256 * 1024];
            if (chunk.length == 0) break;
            CC_SHA256_Update(&ctx, chunk.bytes, (CC_LONG)chunk.length);
        }
    }
    [fh closeFile];
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &ctx);
    char hex[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    static const char *H = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        hex[2 * i] = H[digest[i] >> 4];
        hex[2 * i + 1] = H[digest[i] & 15];
    }
    hex[CC_SHA256_DIGEST_LENGTH * 2] = 0;
    return [NSString stringWithUTF8String:hex];
}
