//
//  KSNetData.m -- 数据通道（WiFi 分块）、发现、服务器、会话
//  KeShare 1.0
//

#import "KeShare.h"
#import "KSInternal.h"
#import <arpa/inet.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <sys/socket.h>
#import <sys/time.h>
#import <unistd.h>
#import <fcntl.h>

#pragma mark - 数据帧常量

#define KS_DATA_MAGIC_LINE   "DATA "
#define KS_CTL_LINE          "CTL "

#pragma mark - 传输注册表（数据通道 <-> 会话 之间的桥）

@implementation KSPendingTransfer
@end

@implementation KSDataRegistry
{
    NSMutableDictionary<NSString *, KSPendingTransfer *> *_map;
    dispatch_queue_t _q;
}
+ (KSDataRegistry *)shared
{
    static KSDataRegistry *r;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ r = [[KSDataRegistry alloc] init]; });
    return r;
}
- (instancetype)init
{
    if ((self = [super init])) {
        _map = [NSMutableDictionary dictionary];
        _q = dispatch_queue_create("keshare.registry", DISPATCH_QUEUE_SERIAL);
    }
    return self;
}
- (void)addPending:(KSPendingTransfer *)p { dispatch_sync(_q, ^{ self->_map[p.transferId] = p; }); }
- (KSPendingTransfer *)pendingForId:(NSString *)tid
{
    __block KSPendingTransfer *p;
    dispatch_sync(_q, ^{ p = self->_map[tid]; });
    return p;
}
- (void)removePendingForId:(NSString *)tid { dispatch_sync(_q, ^{ [self->_map removeObjectForKey:tid]; }); }
@end

#pragma mark - 服务器：监听 47377，接收数据通道

@class KSTcpServerLink;

@interface KSServer () <KSControlLinkDelegate>
@end

@interface KSServer ()
@property (nonatomic, strong) NSMutableArray<KSDevice *> *mutablePeers;
@property (nonatomic, strong) NSMutableDictionary<NSString *, KSSession *> *sessions;
@property (nonatomic, assign) int listenFd;
@property (nonatomic, strong) dispatch_queue_t netQ;
@property (nonatomic, strong) id<KSControlLink> controlLink;   // BLE 广播端
@property (nonatomic, strong) NSNetService *netService;
/// 服务端握手（TCP 接入的 hello，在读线程里同步回包）
- (void)handleServerHello:(NSDictionary *)message link:(KSTcpServerLink *)link;
@end

#pragma mark - 服务端控制链路（accepted socket 上的一条 CTL 会话）

/// 关键：对端连进来的控制通道，所有回包必须写回这条 socket。
/// 旧实现把回包写到全局 BLE 链路，WiFi 回退时握手永远完不成。
@interface KSTcpServerLink : NSObject <KSServerControlLink>
@property (nonatomic, weak) id<KSControlLinkDelegate> delegate;
@property (nonatomic, assign) int fd;
@property (nonatomic, copy) NSString *nonce;
@property (nonatomic, copy, nullable) NSString *peerDid;
@property (nonatomic, copy, nullable) NSString *remoteIp;
- (instancetype)initWithFd:(int)fd remoteIp:(nullable NSString *)ip;
@end

@implementation KSTcpServerLink
{
    NSLock *_sendLock;
    BOOL _open;
}

- (instancetype)initWithFd:(int)fd remoteIp:(NSString *)ip
{
    if ((self = [super init])) {
        _fd = fd;
        _remoteIp = [ip copy] ?: @"";
        _nonce = @"";
        _sendLock = [[NSLock alloc] init];
        _open = YES;
    }
    return self;
}

- (NSString *)transportName { return @"WiFi 回退"; }
- (BOOL)isOpen { return _open; }

- (void)sendMessage:(NSDictionary *)message
{
    NSData *json = [NSJSONSerialization dataWithJSONObject:message options:0 error:NULL];
    if (!json) return;
    uint32_t len = (uint32_t)json.length;
    NSMutableData *frame = [NSMutableData dataWithCapacity:json.length + 4];
    uint8_t hdr[4] = { (uint8_t)(len >> 24), (uint8_t)(len >> 16), (uint8_t)(len >> 8), (uint8_t)len };
    [frame appendBytes:hdr length:4];
    [frame appendData:json];

    [_sendLock lock];
    if (_open && _fd >= 0) {
        KSWriteFull(_fd, frame.bytes, frame.length);
    }
    [_sendLock unlock];

    if (KSConfig.shared.verboseLogging) {
        [[KSLog shared] log:@"CTL→(srv) %@", message[@"t"]];
    }
}

- (void)close
{
    [_sendLock lock];
    _open = NO;
    if (_fd >= 0) {
        shutdown(_fd, SHUT_RDWR);
        close(_fd);
        _fd = -1;
    }
    [_sendLock unlock];
}

@end

@implementation KSServer

+ (KSServer *)shared
{
    static KSServer *s;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ s = [[KSServer alloc] init]; });
    return s;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _mutablePeers = [NSMutableArray array];
        _sessions = [NSMutableDictionary dictionary];
        _listenFd = -1;
        _netQ = dispatch_queue_create("keshare.server", DISPATCH_QUEUE_CONCURRENT);
    }
    return self;
}

- (NSArray<KSDevice *> *)peers { return [self.mutablePeers copy]; }
- (NSArray<KSDevice *> *)knownPeers { return [self.mutablePeers copy]; }

- (void)start
{
    if (self.listenFd >= 0) return;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { [[KSLog shared] error:@"socket() 失败"]; return; }
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof addr);
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((uint16_t)KSConfig.shared.port);

    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0) {
        [[KSLog shared] error:@"端口 %ld 绑定失败，数据通道未启动", (long)KSConfig.shared.port];
        close(fd);
        return;
    }
    listen(fd, 8);
    self.listenFd = fd;
    [[KSLog shared] info:@"数据通道监听 %@:%ld", KSIPv4StringLocal(), (long)KSConfig.shared.port];

    [self publishBonjour];
    [self startControlChannel];
    KSDiscoveryStart();

    dispatch_async(self.netQ, ^{
        while (self.listenFd >= 0) {
            struct sockaddr_in peer;
            socklen_t len = sizeof peer;
            int cfd = accept(self.listenFd, (struct sockaddr *)&peer, &len);
            if (cfd < 0) break;
            dispatch_async(self.netQ, ^{ [self handleConnection:cfd]; });
        }
    });
}

- (void)stop
{
    if (self.listenFd >= 0) {
        shutdown(self.listenFd, SHUT_RDWR);
        close(self.listenFd);
        self.listenFd = -1;
        [[KSLog shared] info:@"数据通道已停止"];
    }
    [self.netService stop];
    self.netService = nil;
    KSDiscoveryStop();
}

- (void)restart
{
    [self stop];
    [self start];
}

- (void)refreshBonjour
{
    [self.netService stop];
    [self publishBonjour];
}

- (void)publishBonjour
{
    self.netService = [[NSNetService alloc] initWithDomain:@"local."
                                                      type:@"_keshare._tcp."
                                                      name:KSConfig.shared.deviceName ?: @"KeShare"
                                                      port:(int)KSConfig.shared.port];
    NSData *txt = [[NSString stringWithFormat:@"v=1&did=%@&caps=%u",
                    KSConfig.shared.deviceId, KSConfig.shared.caps] dataUsingEncoding:NSUTF8StringEncoding];
    self.netService.TXTRecordData = txt;
    [self.netService publish];
}

- (void)startControlChannel
{
    Class bleClass = NSClassFromString(@"KSBleLink");
    if (!bleClass) return;
    id<KSDiscoveryLink> ble = [[bleClass alloc] init];
    ble.delegate = self;
    __weak typeof(self) weakSelf = self;
    ble.onPeerFound = ^(KSDevice *peer, id peripheral) {
        peer.link = peripheral;
        peer.host = KSIPv4StringLocal();
        peer.port = (uint16_t)(KSConfig.shared.port > 0 ? KSConfig.shared.port : KS_DATA_PORT);
        peer.kind = KSDeviceKindUnknown;
        [weakSelf addPeer:peer];
    };
    self.controlLink = ble;
    [ble startAdvertising];
    [ble startScanning];
    [[KSLog shared] info:@"蓝牙广播 + 扫描已启动"];
}

- (id<KSDiscoveryLink>)bleLink
{
    id l = self.controlLink;
    if ([l conformsToProtocol:@protocol(KSDiscoveryLink)]) return (id<KSDiscoveryLink>)l;
    return nil;
}

- (KSSession *)sessionForPeerId:(NSString *)deviceId
{
    KSSession *s = self.sessions[deviceId];
    if (!s) {
        KSDevice *peer = nil;
        for (KSDevice *d in self.mutablePeers) {
            if ([d.deviceId isEqualToString:deviceId]) { peer = d; break; }
        }
        if (!peer) return nil;
        s = [[KSSession alloc] initWithPeer:peer];
        s.delegate = nil;
        [s startAsInitiator:YES];
        self.sessions[deviceId] = s;
    }
    return s;
}

#pragma mark 数据连接处理

- (void)handleConnection:(int)cfd
{
    @autoreleasepool {
        struct timeval tv = { 30, 0 };
        setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        KSSetNoDelay(cfd);

        char line[256];
        NSUInteger i = 0;
        while (i < sizeof(line) - 1) {
            char ch;
            if (read(cfd, &ch, 1) != 1) { close(cfd); return; }
            if (ch == '\n') break;
            line[i++] = ch;
        }
        line[i] = 0;
        NSString *header = [NSString stringWithUTF8String:line];

        // 三路分流：DATA 数据通道 / CTL 控制通道（WiFi 回退）/ 裸长度帧也是控制通道
        if ([header hasPrefix:@"CTL "]) {
            [[KSLog shared] info:@"WiFi 回退控制通道接入"];
            [self serveControlSocket:cfd];
            return;
        }
        if (![header hasPrefix:@"DATA "]) {
            // 首行可能是 4 字节长度帧的开头，交给控制通道处理（把已读字节一起传过去）
            [[KSLog shared] info:@"按裸长度帧处理为控制通道"];
            [self serveControlSocketWithPrefix:cfd prefix:(const uint8_t *)line length:i];
            return;
        }
        NSArray *parts = [header componentsSeparatedByString:@" "];
        if (parts.count < 3) { close(cfd); return; }
        NSString *tid = parts[2];

        KSPendingTransfer *pt = [[KSDataRegistry shared] pendingForId:tid];
        if (!pt) {
            [[KSLog shared] warn:@"未知的传输 id %@", tid];
            close(cfd);
            return;
        }

        // 本机是发送方（对端来拉文件）→ 写数据帧
        if (pt.servePath) {
            [self serveTransfer:pt onSocket:cfd];
            return;
        }

        NSString *tmp = [NSTemporaryDirectory() stringByAppendingPathComponent:
                         [NSString stringWithFormat:@"keshare-%@.part", tid]];
        [[NSFileManager defaultManager] removeItemAtPath:tmp error:NULL];
        [[NSFileManager defaultManager] createFileAtPath:tmp contents:nil attributes:nil];
        NSFileHandle *fh = [NSFileHandle fileHandleForWritingAtPath:tmp];
        if (!fh) { close(cfd); return; }

        uint32_t expectSeq = 0;
        unsigned long long written = 0;
        BOOL ok = YES;
        NSTimeInterval t0 = [NSDate timeIntervalSinceReferenceDate];
        unsigned long long lastBytes = 0;
        NSTimeInterval lastT = t0;

        while (YES) {
            uint8_t hdr[16];
            if (!KSReadFull(cfd, hdr, 16)) { ok = NO; break; }
            uint32_t seq = ((uint32_t)hdr[0] << 24) | ((uint32_t)hdr[1] << 16) |
                           ((uint32_t)hdr[2] << 8) | hdr[3];
            unsigned long long off = 0;
            for (int k = 0; k < 8; k++) off = (off << 8) | hdr[4 + k];
            uint32_t len = ((uint32_t)hdr[12] << 24) | ((uint32_t)hdr[13] << 16) |
                           ((uint32_t)hdr[14] << 8) | hdr[15];

            if (seq != expectSeq || off != written) {
                [[KSLog shared] error:@"数据帧顺序错误 seq=%u off=%llu", seq, off];
                ok = NO;
                break;
            }
            expectSeq++;

            if (len == 0) break;
            if (len > 4u * 1024 * 1024) { ok = NO; break; }

            NSMutableData *buf = [NSMutableData dataWithLength:len];
            if (!KSReadFull(cfd, buf.mutableBytes, len)) { ok = NO; break; }
            [fh writeData:buf];
            written += len;

            pt.done = written;
            KSTransfer *pub = pt.publicTransfer;
            if (pub) {
                // 用户在详情页点了暂停/取消，要在这里停下来
                if (![pub checkpoint]) {
                    [fh closeFile];
                    close(cfd);
                    [[NSFileManager defaultManager] removeItemAtPath:tmp error:NULL];
                    [[KSDataRegistry shared] removePendingForId:tid];
                    [[KSLog shared] info:@"传输 %@ 已取消", tid];
                    return;
                }
                pub.done = written;
                if (pub.state != KSTransferStatePaused) pub.state = KSTransferStateRunning;
                NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
                if (now - lastT >= 0.4) {
                    double inst = (double)(written - lastBytes) / (now - lastT);
                    pub.speedBps = (pub.speedBps > 0) ? (pub.speedBps * 0.6 + inst * 0.4) : inst;
                    lastBytes = written;
                    lastT = now;
                }
                dispatch_async(dispatch_get_main_queue(), ^{
                    [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification
                                                                        object:pub];
                });
            }
        }

        [fh closeFile];
        close(cfd);

        if (!ok) {
            [[NSFileManager defaultManager] removeItemAtPath:tmp error:NULL];
            [[KSLog shared] error:@"传输 %@ 数据阶段失败", tid];
            KSTransfer *pub0 = pt.publicTransfer;
            if (pub0) {
                pub0.state = KSTransferStateFailed;
                pub0.errorText = @"数据通道中断";
                pub0.speedBps = 0;
            }
            [[KSDataRegistry shared] removePendingForId:tid];
            return;
        }

        pt.tempPath = tmp;
        pt.done = written;
        [[KSLog shared] info:@"传输 %@ 收到 %llu 字节，等待校验", tid, written];

        KSTransfer *pub = pt.publicTransfer;
        if (pub) {
            pub.done = written;
            pub.state = KSTransferStateVerifying;
            pub.speedBps = 0;
            dispatch_async(dispatch_get_main_queue(), ^{
                [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
            });
        }
    }
}

#pragma mark WiFi 回退控制通道（服务端）

// 在已接受的 socket 上处理 4 字节长度帧，把 JSON 交给本对象的控制通道逻辑
- (void)serveControlSocket:(int)fd
{
    [self serveControlSocketWithPrefix:fd prefix:NULL length:0];
}

- (void)serveControlSocketWithPrefix:(int)fd prefix:(const uint8_t *)prefix length:(NSUInteger)plen
{
    KSTcpServerLink *link = [[KSTcpServerLink alloc] initWithFd:fd remoteIp:@""];
    link.delegate = self;

    dispatch_async(self.netQ, ^{
        NSMutableData *buf = [NSMutableData data];
        if (plen) [buf appendBytes:prefix length:plen];
        uint8_t tmp[4096];

        while (YES) {
            @autoreleasepool {
                while (buf.length < 4) {
                    ssize_t n = read(fd, tmp, sizeof tmp);
                    if (n <= 0) { [link close]; return; }
                    [buf appendBytes:tmp length:(NSUInteger)n];
                }
                const uint8_t *b = buf.bytes;
                uint32_t len = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) |
                               ((uint32_t)b[2] << 8) | b[3];
                if (len == 0 || len > 65536) {
                    [[KSLog shared] warn:@"控制帧长度非法 %u，断开", len];
                    [link close];
                    return;
                }
                while (buf.length < 4 + (NSUInteger)len) {
                    ssize_t n = read(fd, tmp, sizeof tmp);
                    if (n <= 0) { [link close]; return; }
                    [buf appendBytes:tmp length:(NSUInteger)n];
                }
                NSData *body = [buf subdataWithRange:NSMakeRange(4, len)];
                [buf replaceBytesInRange:NSMakeRange(0, 4 + (NSUInteger)len)
                               withBytes:NULL length:0];

                id obj = [NSJSONSerialization JSONObjectWithData:body options:0 error:NULL];
                if (![obj isKindOfClass:[NSDictionary class]]) continue;
                NSDictionary *msg = (NSDictionary *)obj;

                // hello 必须在读线程里同步处理，否则 list 请求会抢在握手前面
                NSString *kind = msg[@"t"];
                if ([kind isEqualToString:@"hello"]) {
                    [self handleServerHello:msg link:link];
                } else {
                    dispatch_async(dispatch_get_main_queue(), ^{
                        [self controlLink:link didReceiveMessage:msg];
                    });
                }
            }
        }
    });
}

#pragma mark 服务端握手（B 侧：必须回 token，否则 Windows 侧校验直接失败）

- (void)handleServerHello:(NSDictionary *)message link:(KSTcpServerLink *)link
{
    NSString *peerDid = message[@"did"] ?: @"";
    NSString *peerNonce = message[@"nonce"] ?: @"";
    NSString *peerHost = message[@"host"];
    if (peerHost.length) link.remoteIp = peerHost;
    link.peerDid = peerDid;
    if (!link.nonce.length) link.nonce = KSRandomHex(8);

    NSString *token = KSTokenFor(peerDid, peerNonce, KSConfig.shared.deviceId ?: @"", link.nonce);

    KSDevice *d = [KSDevice deviceWithDictionary:message];
    if (peerHost.length) d.host = peerHost;
    if (!d.port) d.port = (uint16_t)([message[@"port"] unsignedShortValue]);
    d.connected = YES;
    // addPeer 会发通知，必须回到主线程，否则表格在后台线程重载会崩
    dispatch_async(dispatch_get_main_queue(), ^{
        [self addPeer:d];
    });

    NSDictionary *ack = @{
        @"t": @"hello_ack", @"v": @(KS_PROTO_VERSION),
        @"did": KSConfig.shared.deviceId ?: @"",
        @"name": KSConfig.shared.deviceName ?: @"iPhone",
        @"kind": @"ios",
        @"osv": UIDevice.currentDevice.systemVersion ?: @"",
        @"port": @(KSConfig.shared.port),
        @"caps": @(KSConfig.shared.caps),
        @"nonce": link.nonce,
        @"token": token,
        @"host": KSIPv4StringLocal()
    };
    [link sendMessage:ack];
    [[KSLog shared] info:@"控制通道接入：%@ (%@) token=%@", d.name, link.remoteIp ?: @"-", token];
}

#pragma mark KSControlLinkDelegate（作为接收端）

- (void)controlLinkDidOpen:(id<KSControlLink>)link {}
- (void)controlLinkDidClose:(id<KSControlLink>)link {}
- (void)controlLink:(id<KSControlLink>)link didFailWithError:(NSString *)error
{
    [[KSLog shared] error:@"控制通道错误: %@", error];
}

- (void)controlLink:(id<KSControlLink>)link didReceiveMessage:(NSDictionary *)message
{
    NSString *t = message[@"t"];
    if ([t isEqualToString:@"hello"]) {
        // 这条路径只在 BLE 控制通道上用得到；TCP 接入的 hello 在读线程里就处理完了
        KSDevice *d = [KSDevice deviceWithDictionary:message];
        d.host = message[@"host"] ?: KSIPv4StringLocal();
        d.connected = YES;
        [self addPeer:d];
        NSString *nonce = KSRandomHex(8);
        NSString *token = KSTokenFor(message[@"did"] ?: @"", message[@"nonce"] ?: @"",
                                     KSConfig.shared.deviceId ?: @"", nonce);
        NSDictionary *ack = @{
            @"t": @"hello_ack", @"v": @(KS_PROTO_VERSION),
            @"did": KSConfig.shared.deviceId ?: @"",
            @"name": KSConfig.shared.deviceName ?: @"iPhone",
            @"kind": @"ios",
            @"osv": UIDevice.currentDevice.systemVersion ?: @"",
            @"port": @(KSConfig.shared.port),
            @"caps": @(KSConfig.shared.caps),
            @"nonce": nonce,
            @"token": token,
            @"host": KSIPv4StringLocal()
        };
        [link sendMessage:ack];
    } else if ([t isEqualToString:@"list"]) {
        [self handleListRequest:message link:link];
    } else if ([t isEqualToString:@"get"]) {
        [self handleGetRequest:message link:link];
    } else if ([t isEqualToString:@"put_offer"]) {
        [self handlePutOffer:message link:link];
    } else if ([t isEqualToString:@"hash"]) {
        [self handleHashMessage:message link:link];
    } else if ([t isEqualToString:@"hash_resp"]) {
        [self handleHashAck:message];
    } else if ([t isEqualToString:@"clip"]) {
        if ([[message[@"mode"] description] isEqualToString:@"get"]) {
            [link sendMessage:@{@"t": @"clip", @"mode": @"set",
                                @"text": UIPasteboard.generalPasteboard.string ?: @""}];
        } else if ([message[@"text"] isKindOfClass:[NSString class]]) {
            UIPasteboard.generalPasteboard.string = message[@"text"];
        }
    } else if ([t isEqualToString:@"bye"]) {
        [link close];
    }
}

- (void)handleHashMessage:(NSDictionary *)message link:(id<KSControlLink>)link
{
    NSString *tid = message[@"id"];
    NSString *expect = message[@"sha_ke_1024"] ?: message[@"hash"];
    KSPendingTransfer *pt = [[KSDataRegistry shared] pendingForId:tid];
    if (!pt || !pt.tempPath) {
        [link sendMessage:@{@"t": @"hash_resp", @"id": tid ?: @"", @"ok": @NO, @"err": @"notfound"}];
        return;
    }
    NSError *err = nil;
    NSString *actual = KSHashOfFile(pt.tempPath, &err);
    BOOL ok = actual && [actual isEqualToString:expect];
    KSTransfer *pub = pt.publicTransfer;

    if (ok) {
        NSString *dest = pt.finalPath;
        if (!dest) {
            NSString *dir = KSConfig.shared.receivePath;
            [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES
                                                   attributes:nil error:NULL];
            dest = [dir stringByAppendingPathComponent:pt.transferId];
        }
        [[NSFileManager defaultManager] removeItemAtPath:dest error:NULL];
        [[NSFileManager defaultManager] moveItemAtPath:pt.tempPath toPath:dest error:&err];
        [[KSLog shared] info:@"传输 %@ 校验通过 -> %@", tid, dest.lastPathComponent];
        [link sendMessage:@{@"t": @"hash_resp", @"id": tid ?: @"", @"ok": @YES}];
        if (pub) {
            pub.state = KSTransferStateDone;
            pub.verified = YES;
            pub.done = pub.total = KSFileSize(dest);
            pub.path = dest;
            pub.speedBps = 0;
            dispatch_async(dispatch_get_main_queue(), ^{
                [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
            });
        }
    } else {
        [[NSFileManager defaultManager] removeItemAtPath:pt.tempPath error:NULL];
        [[KSLog shared] error:@"传输 %@ 校验失败（期望 %@… 实际 %@…）", tid,
            [expect substringToIndex:MIN(16u, expect.length)],
            [actual substringToIndex:MIN(16u, actual.length)]];
        [link sendMessage:@{@"t": @"hash_resp", @"id": tid ?: @"", @"ok": @NO, @"err": @"checksum"}];
        if (pub) {
            pub.state = KSTransferStateFailed;
            pub.errorText = @"校验失败";
            pub.speedBps = 0;
            dispatch_async(dispatch_get_main_queue(), ^{
                [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
            });
        }
    }
    [[KSDataRegistry shared] removePendingForId:tid];
}

#pragma mark 历史设备自动连接

- (void)autoConnectKnownPeers
{
    if (!KSConfig.shared.autoConnect) {
        [[KSLog shared] info:@"自动连接未开启"];
        return;
    }
    NSArray<NSDictionary *> *known = [KSConfig.shared knownDevices];
    if (known.count == 0) return;
    NSInteger limit = 0;
    __weak typeof(self) weakSelf = self;
    for (NSDictionary *item in known) {
        if (limit >= 3) break;
        NSString *did = item[@"did"] ?: @"";
        NSString *host = item[@"host"] ?: @"";
        if (did.length == 0 || host.length == 0) continue;
        if ([did isEqualToString:KSConfig.shared.deviceId]) continue;
        if ([self.mutablePeers indexOfObjectPassingTest:^BOOL(KSDevice *obj, NSUInteger idx, BOOL *stop) {
                return [obj.deviceId isEqualToString:did] && obj.connected;
            }] != NSNotFound) continue;

        KSDevice *peer = [[KSDevice alloc] init];
        peer.deviceId = did;
        peer.name = item[@"name"] ?: did;
        peer.host = host;
        peer.port = (uint16_t)([item[@"port"] unsignedShortValue] ?: KSConfig.shared.port);
        peer.kind = KSDeviceKindWindows;
        [weakSelf addPeer:peer];
        [[KSLog shared] info:@"自动连接历史设备 %@ (%@)", peer.name, host];

        double delay = 0.6 + 0.8 * (double)limit;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(delay * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            KSSession *session = [[KSSession alloc] initWithPeer:peer];
            [session startAsInitiator:YES];
            [weakSelf.sessions setObject:session forKey:did];
        });
        limit++;
    }
}

- (void)addPeer:(KSDevice *)d
{
    if (!d.deviceId) return;
    for (NSUInteger i = 0; i < self.mutablePeers.count; i++) {
        if ([self.mutablePeers[i].deviceId isEqualToString:d.deviceId]) {
            self.mutablePeers[i] = d;
            [[NSNotificationCenter defaultCenter] postNotificationName:KSPeersDidChangeNotification object:nil];
            return;
        }
    }
    [self.mutablePeers addObject:d];
    [[KSLog shared] info:@"发现设备 %@ (%@)", d.name, d.kindLabel];
    if (d.host.length) [KSConfig.shared rememberDevice:d];
    [[NSNotificationCenter defaultCenter] postNotificationName:KSPeersDidChangeNotification object:nil];
}

@end









