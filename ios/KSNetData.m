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

@interface KSServer () <KSControlLinkDelegate>
@end

@interface KSServer ()
@property (nonatomic, strong) NSMutableArray<KSDevice *> *mutablePeers;
@property (nonatomic, strong) NSMutableDictionary<NSString *, KSSession *> *sessions;
@property (nonatomic, assign) int listenFd;
@property (nonatomic, strong) dispatch_queue_t netQ;
@property (nonatomic, strong) id<KSControlLink> controlLink;   // BLE 广播端
@property (nonatomic, strong) NSNetService *netService;
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
                pub.done = written;
                pub.state = KSTransferStateRunning;
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
    dispatch_async(self.netQ, ^{
        uint8_t buf[4];
        NSMutableData *prefixBuf = plen ? [NSMutableData dataWithBytes:prefix length:plen] : nil;
        while (YES) {
            uint32_t len = 0;
            NSUInteger have = 0;
            while (have < 4) {
                if (prefixBuf.length) {
                    uint8_t b = ((const uint8_t *)prefixBuf.bytes)[0];
                    buf[have++] = b;
                    [prefixBuf replaceBytesInRange:NSMakeRange(0, 1) withBytes:NULL length:0];
                } else {
                    ssize_t n = read(fd, buf + have, 4 - have);
                    if (n <= 0) { close(fd); return; }
                    have += (NSUInteger)n;
                }
            }
            len = ((uint32_t)buf[0] << 24) | ((uint32_t)buf[1] << 16) |
                  ((uint32_t)buf[2] << 8) | buf[3];
            if (len == 0 || len > 65536) { close(fd); return; }
            NSMutableData *body = [NSMutableData dataWithLength:len];
            NSUInteger got = 0;
            if (prefixBuf.length) {
                NSUInteger take = MIN(prefixBuf.length, (NSUInteger)len);
                [body replaceBytesInRange:NSMakeRange(0, take) withBytes:prefixBuf.bytes length:take];
                got = take;
                [prefixBuf setLength:0];
            }
            while (got < len) {
                ssize_t n = read(fd, (uint8_t *)body.mutableBytes + got, len - got);
                if (n <= 0) { close(fd); return; }
                got += (NSUInteger)n;
            }
            id obj = [NSJSONSerialization JSONObjectWithData:body options:0 error:NULL];
            if ([obj isKindOfClass:[NSDictionary class]]) {
                dispatch_async(dispatch_get_main_queue(), ^{
                    [self controlLink:self.controlLink didReceiveMessage:obj];
                });
            }
        }
    });
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
        KSDevice *d = [KSDevice deviceWithDictionary:message];
        d.host = message[@"host"] ?: KSIPv4StringLocal();
        d.connected = YES;
        [self addPeer:d];
        NSDictionary *ack = @{
            @"t": @"hello_ack", @"v": @(KS_PROTO_VERSION),
            @"did": KSConfig.shared.deviceId ?: @"",
            @"name": KSConfig.shared.deviceName ?: @"iPhone",
            @"kind": @"ios",
            @"osv": UIDevice.currentDevice.systemVersion ?: @"",
            @"port": @(KSConfig.shared.port),
            @"caps": @(KSConfig.shared.caps),
            @"nonce": KSRandomHex(16),
            @"host": KSIPv4StringLocal()
        };
        [link sendMessage:ack];
    } else if ([t isEqualToString:@"get"]) {
        [self handleGetRequest:message link:link];
    } else if ([t isEqualToString:@"put_offer"]) {
        [self handlePutOffer:message link:link];
    } else if ([t isEqualToString:@"hash"]) {
        [self handleHashMessage:message link:link];
    } else if ([t isEqualToString:@"hash_resp"]) {
        [self handleHashAck:message];
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
            pub.done = pub.total = KSFileSize(dest);
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
            dispatch_async(dispatch_get_main_queue(), ^{
                [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
            });
        }
    }
    [[KSDataRegistry shared] removePendingForId:tid];
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
    [[NSNotificationCenter defaultCenter] postNotificationName:KSPeersDidChangeNotification object:nil];
}

@end









