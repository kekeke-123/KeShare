//
//  KSDisc.m -- UDP 发现（无需路由器/蓝牙）
//  KeShare 1.0.2
//
//  §6.2：两端在 47377/UDP 上广播 probe，收到 probe 的一方单播回 announce。
//  iOS 侧此前往返只有 BLE，Windows 用 UDP 广播永远发现不了 iOS，这里补齐。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <arpa/inet.h>
#import <netinet/in.h>
#import <sys/socket.h>
#import <unistd.h>
#import <ifaddrs.h>

@interface KSDisc : NSObject
@property (class, nonatomic, readonly) KSDisc *shared;
- (void)start;
- (void)stop;
@end

@implementation KSDisc
{
    int _fd;
    dispatch_queue_t _q;
    dispatch_source_t _timer;
    dispatch_source_t _reader;
    BOOL _running;
}

+ (KSDisc *)shared
{
    static KSDisc *d;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ d = [[KSDisc alloc] init]; });
    return d;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _fd = -1;
        _q = dispatch_queue_create("keshare.disc", DISPATCH_QUEUE_SERIAL);
    }
    return self;
}

- (void)start
{
    if (_running) return;
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) { [[KSLog shared] error:@"UDP socket 失败"]; return; }

    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &one, sizeof one);

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(KS_DATA_PORT);
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        [[KSLog shared] error:@"UDP 47377 绑定失败，发现功能不可用"];
        close(fd);
        return;
    }
    _fd = fd;
    _running = YES;

    // 读循环
    dispatch_source_t src = dispatch_source_create(DISPATCH_SOURCE_TYPE_READ, (uintptr_t)fd, 0, _q);
    __weak typeof(self) weakSelf = self;
    dispatch_source_set_event_handler(src, ^{ [weakSelf drain]; });
    dispatch_resume(src);
    _reader = src;

    // 每 5 秒广播一次 probe，并主动 announce
    dispatch_source_t t = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, _q);
    dispatch_source_set_timer(t, dispatch_time(DISPATCH_TIME_NOW, 0),
                              5ull * NSEC_PER_SEC, 1ull * NSEC_PER_SEC);
    dispatch_source_set_event_handler(t, ^{ [weakSelf broadcast]; });
    dispatch_resume(t);
    _timer = t;

    [[KSLog shared] info:@"UDP 发现已启动（47377）"];
}

- (void)stop
{
    if (!_running) return;
    _running = NO;
    if (_timer) { dispatch_source_cancel(_timer); _timer = nil; }
    if (_reader) { dispatch_source_cancel(_reader); _reader = nil; }
    if (_fd >= 0) { close(_fd); _fd = -1; }
    [[KSLog shared] info:@"UDP 发现已停止"];
}

#pragma mark - 收发

- (NSDictionary *)announcePayload
{
    return @{
        @"ks": @1,
        @"t": @"announce",
        @"did": KSConfig.shared.deviceId ?: @"",
        @"name": KSConfig.shared.deviceName ?: @"iPhone",
        @"kind": @"ios",
        @"osv": UIDevice.currentDevice.systemVersion ?: @"",
        @"port": @(KSConfig.shared.port),
        @"caps": @(KSConfig.shared.caps),
        @"host": KSIPv4StringLocal()
    };
}

- (void)broadcast
{
    if (_fd < 0) return;
    NSDictionary *probe = @{
        @"ks": @1,
        @"t": @"probe",
        @"did": KSConfig.shared.deviceId ?: @"",
        @"name": KSConfig.shared.deviceName ?: @"iPhone",
        @"port": @(KSConfig.shared.port),
        @"caps": @(KSConfig.shared.caps),
        @"host": KSIPv4StringLocal()
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:probe options:0 error:NULL];
    if (!data) return;

    struct sockaddr_in dst;
    memset(&dst, 0, sizeof dst);
    dst.sin_family = AF_INET;
    dst.sin_port = htons(KS_DATA_PORT);

    // 全局广播
    dst.sin_addr.s_addr = htonl(INADDR_BROADCAST);
    sendto(_fd, data.bytes, data.length, 0, (struct sockaddr *)&dst, sizeof dst);

    // 各网段的定向广播（热点 / 多网卡更可靠）
    struct ifaddrs *ifa = NULL;
    if (getifaddrs(&ifa) == 0) {
        for (struct ifaddrs *p = ifa; p; p = p->ifa_next) {
            if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET) continue;
            if (!p->ifa_broadaddr) continue;
            struct sockaddr_in *b = (struct sockaddr_in *)p->ifa_broadaddr;
            if (b->sin_addr.s_addr == 0) continue;
            dst.sin_addr = b->sin_addr;
            sendto(_fd, data.bytes, data.length, 0, (struct sockaddr *)&dst, sizeof dst);
        }
        freeifaddrs(ifa);
    }
}

- (void)drain
{
    uint8_t buf[1024];
    struct sockaddr_in from;
    socklen_t flen = sizeof from;

    while (_fd >= 0) {
        ssize_t n = recvfrom(_fd, buf, sizeof buf - 1, 0, (struct sockaddr *)&from, &flen);
        if (n <= 0) break;
        buf[n] = 0;
        NSData *d = [NSData dataWithBytes:buf length:(NSUInteger)n];
        id obj = [NSJSONSerialization JSONObjectWithData:d options:0 error:NULL];
        if (![obj isKindOfClass:[NSDictionary class]]) continue;
        NSDictionary *msg = obj;

        // 兼容两套标记：ks 是统一字段，wb 是旧版 Windows 字段
        NSInteger magic = [msg[@"ks"] integerValue] ?: [msg[@"wb"] integerValue];
        if (magic != 1) continue;

        NSString *myDid = KSConfig.shared.deviceId ?: @"";
        NSString *did = msg[@"did"] ?: @"";
        if (did.length && [did isEqualToString:myDid]) continue;   // 自己发的

        if ([msg[@"t"] isEqualToString:@"probe"]) {
            NSData *reply = [NSJSONSerialization dataWithJSONObject:[self announcePayload]
                                                            options:0 error:NULL];
            if (reply) sendto(_fd, reply.bytes, reply.length, 0,
                              (struct sockaddr *)&from, sizeof from);
            continue;
        }
        if ([msg[@"t"] isEqualToString:@"announce"]) {
            KSDevice *peer = [KSDevice deviceWithDictionary:msg];
            char ipbuf[INET_ADDRSTRLEN];
            if (inet_ntop(AF_INET, &from.sin_addr, ipbuf, sizeof ipbuf)) {
                peer.host = msg[@"host"] ?: [NSString stringWithUTF8String:ipbuf];
            }
            if (!peer.host.length) peer.host = [NSString stringWithUTF8String:ipbuf];
            if (peer.port == 0) peer.port = (uint16_t)KSConfig.shared.port;
            [[KSLog shared] info:@"UDP 发现 %@ (%@)", peer.name, peer.host];
            dispatch_async(dispatch_get_main_queue(), ^{
                [[KSServer shared] addPeer:peer];
            });
        }
    }
}

@end

#pragma mark - 供 KSServer 调用

void KSDiscoveryStart(void) { [[KSDisc shared] start]; }
void KSDiscoveryStop(void) { [[KSDisc shared] stop]; }
