//
//  KSInternal.m -- 内部共享实现
//

#import "KSInternal.h"
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
