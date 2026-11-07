//
//  KSSession.m -- 会话：控制链路 + 数据通道 + 完整性校验
//  KeShare 1.0
//
//  规则（见 PROTOCOL.md §4.0）：**接收方主动连接发送方**。
//    - 下载：本机是接收方，连接对端 47377 读取数据帧
//    - 上传：本机是发送方，对端连过来后由 KSServer 写出数据帧
//  CTL 上交换 SHA-KE-1024，不匹配即删除临时文件。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <arpa/inet.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <sys/socket.h>
#import <sys/time.h>
#import <unistd.h>

#pragma mark - WiFi 回退控制链路

@interface KSWifiControlLink : NSObject <KSControlLink>
@property (nonatomic, weak) id<KSControlLinkDelegate> delegate;
@property (nonatomic, readonly) BOOL isOpen;
@property (nonatomic, readonly) NSString *transportName;
- (instancetype)initWithHost:(NSString *)host port:(uint16_t)port token:(NSString *)token;
@end

@implementation KSWifiControlLink
{
    int _fd;
    NSMutableData *_buf;
    dispatch_queue_t _q;
    BOOL _open;
}

- (instancetype)initWithHost:(NSString *)host port:(uint16_t)port token:(NSString *)token
{
    if ((self = [super init])) {
        _fd = -1;
        _buf = [NSMutableData data];
        _q = dispatch_queue_create("keshare.ctlwifi", DISPATCH_QUEUE_SERIAL);
        [self connectHost:host port:port token:token];
    }
    return self;
}

- (NSString *)transportName { return @"WiFi 回退"; }
- (BOOL)isOpen { return _open; }

- (void)connectHost:(NSString *)host port:(uint16_t)port token:(NSString *)token
{
    if (host.length == 0) return;
    NSString *h = host;
    uint16_t p = port ?: (uint16_t)KSConfig.shared.port;
    dispatch_async(_q, ^{
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) return;
        struct sockaddr_in addr;
        memset(&addr, 0, sizeof addr);
        addr.sin_family = AF_INET;
        addr.sin_port = htons(p);
        if (inet_pton(AF_INET, h.UTF8String, &addr.sin_addr) != 1) {
            close(fd);
            return;
        }
        if (connect(fd, (struct sockaddr *)&addr, sizeof addr) != 0) {
            close(fd);
            dispatch_async(dispatch_get_main_queue(), ^{
                if ([self.delegate respondsToSelector:@selector(controlLink:didFailWithError:)]) {
                    [self.delegate controlLink:self didFailWithError:@"无法连接控制通道"];
                }
            });
            return;
        }
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        struct timeval tv = { 15, 0 };
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

        NSString *line = [NSString stringWithFormat:@"CTL %@\n", token ?: @"-"];
        NSData *d = [line dataUsingEncoding:NSUTF8StringEncoding];
        write(fd, d.bytes, d.length);

        self->_fd = fd;
        self->_open = YES;
        dispatch_async(dispatch_get_main_queue(), ^{
            if ([self.delegate respondsToSelector:@selector(controlLinkDidOpen:)]) {
                [self.delegate controlLinkDidOpen:self];
            }
        });
        [self readLoop];
    });
}

- (void)readLoop
{
    while (_fd >= 0) {
        uint8_t hdr[4];
        ssize_t n = read(_fd, hdr, 4);
        if (n <= 0) break;
        uint32_t len = ((uint32_t)hdr[0] << 24) | ((uint32_t)hdr[1] << 16) |
                       ((uint32_t)hdr[2] << 8) | hdr[3];
        if (len == 0 || len > 65536) break;
        NSMutableData *body = [NSMutableData dataWithLength:len];
        if (!KSReadFull(_fd, body.mutableBytes, len)) break;
        id obj = [NSJSONSerialization JSONObjectWithData:body options:0 error:NULL];
        if ([obj isKindOfClass:[NSDictionary class]]) {
            dispatch_async(dispatch_get_main_queue(), ^{
                if ([self.delegate respondsToSelector:@selector(controlLink:didReceiveMessage:)]) {
                    [self.delegate controlLink:self didReceiveMessage:obj];
                }
            });
        }
    }
    _open = NO;
    if (_fd >= 0) { close(_fd); _fd = -1; }
    dispatch_async(dispatch_get_main_queue(), ^{
        if ([self.delegate respondsToSelector:@selector(controlLinkDidClose:)]) {
            [self.delegate controlLinkDidClose:self];
        }
    });
}

- (void)sendMessage:(NSDictionary *)message
{
    NSData *json = [NSJSONSerialization dataWithJSONObject:message options:0 error:NULL];
    if (!json || _fd < 0) return;
    uint32_t len = (uint32_t)json.length;
    uint8_t hdr[4] = { (uint8_t)(len >> 24), (uint8_t)(len >> 16), (uint8_t)(len >> 8), (uint8_t)len };
    NSMutableData *d = [NSMutableData dataWithBytes:hdr length:4];
    [d appendData:json];
    dispatch_async(_q, ^{
        if (self->_fd >= 0) write(self->_fd, d.bytes, d.length);
    });
}

- (void)close
{
    if (_fd >= 0) { shutdown(_fd, SHUT_RDWR); close(_fd); _fd = -1; }
    _open = NO;
}

@end

#pragma mark - 会话

@interface KSSession ()
@property (nonatomic, readwrite) KSDevice *peer;
@property (nonatomic, readwrite) id<KSControlLink> link;
@property (nonatomic, readwrite) NSArray<NSDictionary *> *lastListing;
@property (nonatomic, copy) NSString *token;
@property (nonatomic, copy) NSString *myNonce;
@property (nonatomic, copy) NSString *peerNonce;
@property (nonatomic, assign) BOOL initiator;
@property (nonatomic, strong) NSMutableArray<KSTransfer *> *transfers;
@property (nonatomic, strong) NSMutableDictionary<NSString *, NSString *> *localTemp;
@property (nonatomic, strong) NSMutableDictionary<NSString *, NSString *> *localDest;
@end

@implementation KSSession

- (instancetype)initWithPeer:(KSDevice *)peer
{
    if ((self = [super init])) {
        _peer = peer;
        _transfers = [NSMutableArray array];
        _localTemp = [NSMutableDictionary dictionary];
        _localDest = [NSMutableDictionary dictionary];
        _lastListing = @[];
        _myNonce = KSRandomHex(16);
    }
    return self;
}

- (void)startAsInitiator:(BOOL)initiator
{
    self.initiator = initiator;

    // 优先用「已扫描到的蓝牙外设」直连；否则退回 WiFi 控制通道
    id<KSDiscoveryLink> ble = [KSServer shared].bleLink;
    Class peripheralClass = NSClassFromString(@"CBPeripheral");
    BOOL canUseBLE = ble && self.peer.link && peripheralClass &&
                     [self.peer.link isKindOfClass:peripheralClass];
    if (canUseBLE) {
        self.link = ble;
        self.link.delegate = self;
        [ble connectPeripheral:self.peer.link];
        [[KSLog shared] info:@"会话使用蓝牙控制通道（%@）", self.peer.name];
    }
    if (!self.link) {
        self.link = [[KSWifiControlLink alloc] initWithHost:self.peer.host
                                                      port:self.peer.port
                                                     token:KSRandomHex(8)];
        self.link.delegate = self;
        [[KSLog shared] info:@"会话使用 WiFi 回退控制通道 %@:%u", self.peer.host, self.peer.port];
    }
    if (initiator) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.4 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            [self sendHello];
        });
    }
}

- (void)sendHello
{
    [self.link sendMessage:@{
        @"t": @"hello", @"v": @(KS_PROTO_VERSION),
        @"did": KSConfig.shared.deviceId ?: @"",
        @"name": KSConfig.shared.deviceName ?: @"iPhone",
        @"kind": @"ios",
        @"osv": UIDevice.currentDevice.systemVersion ?: @"",
        @"port": @(KSConfig.shared.port),
        @"caps": @(KSConfig.shared.caps),
        @"nonce": self.myNonce ?: KSRandomHex(16),
        @"host": KSIPv4StringLocal()
    }];
}

- (NSString *)computeTokenWithPeerDid:(NSString *)peerDid peerNonce:(NSString *)peerNonce myDid:(NSString *)myDid
{
    NSMutableData *d = [NSMutableData data];
    [d appendBytes:"KS1-CTL\0" length:8];
    if (self.initiator) {
        [d appendData:[myDid dataUsingEncoding:NSUTF8StringEncoding]];
        [d appendData:[self.myNonce dataUsingEncoding:NSUTF8StringEncoding]];
        [d appendData:[peerDid dataUsingEncoding:NSUTF8StringEncoding]];
        [d appendData:[peerNonce dataUsingEncoding:NSUTF8StringEncoding]];
    } else {
        [d appendData:[peerDid dataUsingEncoding:NSUTF8StringEncoding]];
        [d appendData:[peerNonce dataUsingEncoding:NSUTF8StringEncoding]];
        [d appendData:[myDid dataUsingEncoding:NSUTF8StringEncoding]];
        [d appendData:[self.myNonce dataUsingEncoding:NSUTF8StringEncoding]];
    }
    NSString *hex = KSSHA256HexOfData(d);
    return [hex substringToIndex:16];
}

#pragma mark KSControlLinkDelegate

- (void)controlLinkDidOpen:(id<KSControlLink>)link
{
    [[KSLog shared] info:@"控制通道已建立（%@）→ %@", link.transportName, self.peer.name];
    self.peer.connected = YES;
    if (self.initiator) [self sendHello];
    if ([self.delegate respondsToSelector:@selector(session:didUpdatePeer:)]) {
        [self.delegate session:self didUpdatePeer:self.peer];
    }
}

- (void)controlLink:(id<KSControlLink>)link didFailWithError:(NSString *)error
{
    [[KSLog shared] error:@"控制通道失败: %@", error];
    if ([self.delegate respondsToSelector:@selector(session:didFail:)]) {
        [self.delegate session:self didFail:error];
    }
}

- (void)controlLinkDidClose:(id<KSControlLink>)link
{
    self.peer.connected = NO;
    [[KSLog shared] info:@"控制通道关闭：%@", self.peer.name];
    if ([self.delegate respondsToSelector:@selector(session:didUpdatePeer:)]) {
        [self.delegate session:self didUpdatePeer:self.peer];
    }
}

- (void)controlLink:(id<KSControlLink>)link didReceiveMessage:(NSDictionary *)msg
{
    NSString *t = msg[@"t"];
    if (KSConfig.shared.verboseLogging) [[KSLog shared] log:@"CTL← %@", t];

    if ([t isEqualToString:@"hello"]) {
        self.peerNonce = msg[@"nonce"];
        KSDevice *d = [KSDevice deviceWithDictionary:msg];
        if (d.host.length) self.peer.host = d.host;
        if (d.port) self.peer.port = d.port;
        self.token = [self computeTokenWithPeerDid:msg[@"did"] ?: @""
                                        peerNonce:msg[@"nonce"] ?: @""
                                             myDid:KSConfig.shared.deviceId ?: @""];
        [self.link sendMessage:@{
            @"t": @"hello_ack", @"v": @(KS_PROTO_VERSION),
            @"did": KSConfig.shared.deviceId ?: @"",
            @"name": KSConfig.shared.deviceName ?: @"iPhone",
            @"kind": @"ios",
            @"osv": UIDevice.currentDevice.systemVersion ?: @"",
            @"port": @(KSConfig.shared.port),
            @"caps": @(KSConfig.shared.caps),
            @"nonce": self.myNonce,
            @"token": self.token ?: @"",
            @"host": KSIPv4StringLocal()
        }];
        self.peer.connected = YES;
        if ([self.delegate respondsToSelector:@selector(session:didUpdatePeer:)]) {
            [self.delegate session:self didUpdatePeer:self.peer];
        }
        return;
    }

    if ([t isEqualToString:@"hello_ack"]) {
        self.peerNonce = msg[@"nonce"];
        self.token = msg[@"token"];
        if (!self.token) {
            self.token = [self computeTokenWithPeerDid:msg[@"did"] ?: @""
                                            peerNonce:msg[@"nonce"] ?: @""
                                                 myDid:KSConfig.shared.deviceId ?: @""];
        }
        if ([msg[@"host"] isKindOfClass:[NSString class]]) self.peer.host = msg[@"host"];
        if (msg[@"port"]) self.peer.port = (uint16_t)[msg[@"port"] unsignedShortValue];
        self.peer.connected = YES;
        [[KSLog shared] info:@"握手完成：%@ (%@:%u) token=%@",
            self.peer.name, self.peer.host, self.peer.port, self.token];
        [self requestListing];
        if ([self.delegate respondsToSelector:@selector(session:didUpdatePeer:)]) {
            [self.delegate session:self didUpdatePeer:self.peer];
        }
        return;
    }

    if ([t isEqualToString:@"list_resp"]) {
        self.lastListing = msg[@"files"] ?: @[];
        [[NSNotificationCenter defaultCenter] postNotificationName:KSPeersDidChangeNotification object:nil];
        return;
    }

    if ([t isEqualToString:@"get_resp"]) {
        BOOL ok = [msg[@"ok"] boolValue];
        if (!ok) {
            [self failTransferId:msg[@"id"] reason:msg[@"err"] ?: @"拒绝"];
            return;
        }
        [self beginReceivingTransferId:msg[@"id"]
                                  name:msg[@"name"] ?: @"file"
                                  size:[msg[@"size"] unsignedLongLongValue]];
        return;
    }

    if ([t isEqualToString:@"put_resp"]) {
        // 对端接受推送后，等它连过来读取（KSServer 负责发送）
        return;
    }

    if ([t isEqualToString:@"hash"]) {
        [self verifyIncomingHash:msg];
        return;
    }

    if ([t isEqualToString:@"hash_resp"]) {
        NSString *tid = msg[@"id"];
        KSTransfer *tr = [self transferWithId:tid] ?: [[KSTransferCenter shared] transferWithId:tid];
        BOOL ok = [msg[@"ok"] boolValue];
        if (tr) {
            tr.state = ok ? KSTransferStateDone : KSTransferStateFailed;
            tr.verified = ok;
            tr.speedBps = 0;
            if (!ok) tr.errorText = msg[@"err"] ?: @"校验失败";
        }
        [[KSDataRegistry shared] removePendingForId:tid ?: @""];
        [[KSLog shared] info:@"上传 %@ %@", tid, ok ? @"校验通过" : @"校验失败"];
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:tr];
        return;
    }

    if ([t isEqualToString:@"clip"]) {
        if ([[msg[@"mode"] description] isEqualToString:@"get"]) {
            NSString *text = UIPasteboard.generalPasteboard.string ?: @"";
            [self.link sendMessage:@{@"t": @"clip", @"mode": @"set", @"text": text}];
        } else if ([msg[@"text"] isKindOfClass:[NSString class]]) {
            UIPasteboard.generalPasteboard.string = msg[@"text"];
            [[KSLog shared] info:@"收到剪贴板 %lu 字符", (unsigned long)[msg[@"text"] length]];
        }
        return;
    }

    if ([t isEqualToString:@"bye"]) { [self close]; return; }
}

#pragma mark 请求

- (void)requestListing
{
    [self requestListingPath:@""];
}

- (void)requestListingPath:(NSString *)path
{
    self.lastListing = @[];
    [self.link sendMessage:@{ @"t": @"list", @"token": self.token ?: @"",
                              @"path": path ?: @"" }];
}

- (void)downloadPath:(NSString *)path
{
    NSString *tid = [KSRandomHex(4) lowercaseString];
    KSTransfer *tr = [[KSTransfer alloc] init];
    tr.transferId = tid;
    tr.fileName = path.lastPathComponent;
    tr.path = path;
    tr.peerName = self.peer.name;
    tr.direction = KSTransferDirectionDownload;
    tr.state = KSTransferStateWaiting;
    [_transfers addObject:tr];
    [[KSTransferCenter shared] track:tr];
    [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:tr];
    [self.link sendMessage:@{ @"t": @"get", @"token": self.token ?: @"", @"id": tid, @"path": path }];
    [[KSLog shared] info:@"请求下载 %@ (id=%@)", path, tid];
}

- (void)uploadFileAtPath:(NSString *)localPath
{
    [self uploadFileAtPath:localPath asName:nil];
}

- (void)uploadFileAtPath:(NSString *)localPath asName:(NSString *)name
{
    // 对端保存名可以改，但必须是安全的单层文件名
    NSString *safeName = [name stringByTrimmingCharactersInSet:
                          [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (safeName.length == 0) safeName = localPath.lastPathComponent;
    if (!KSIsValidRelativePath(safeName) || [safeName containsString:@"/"]) {
        [[KSLog shared] warn:@"上传文件名非法：%@，改用原名", name];
        safeName = localPath.lastPathComponent;
    }

    NSString *tid = [KSRandomHex(4) lowercaseString];
    KSTransfer *tr = [[KSTransfer alloc] init];
    tr.transferId = tid;
    tr.fileName = safeName;
    tr.path = localPath;
    tr.peerName = self.peer.name;
    tr.direction = KSTransferDirectionUpload;
    tr.total = [self fileSize:localPath];
    tr.state = KSTransferStateWaiting;
    [_transfers addObject:tr];
    [[KSTransferCenter shared] track:tr];
    [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:tr];

    // 登记为“待发送”：对端连过来后由 KSServer 写数据帧（servePath 是关键，缺了它就变成自己去收）
    KSPendingTransfer *p = [[KSPendingTransfer alloc] init];
    p.transferId = tid;
    p.servePath = localPath;
    p.requestLink = self.link;
    p.publicTransfer = tr;
    p.totalSize = tr.total;
    [[KSDataRegistry shared] addPending:p];

    // host/port 必须带上：对端要用它回连我们的数据端口
    [self.link sendMessage:@{ @"t": @"put_offer", @"token": self.token ?: @"", @"id": tid,
                              @"name": safeName, @"size": @(tr.total),
                              @"did": KSConfig.shared.deviceId ?: @"",
                              @"host": KSIPv4StringLocal(),
                              @"port": @(KSConfig.shared.port) }];
    [[KSLog shared] info:@"请求上传 %@ (id=%@, %llu 字节)", safeName, tid, tr.total];
}

#pragma mark 暂停 / 继续 / 取消

- (BOOL)pauseTransferWithId:(NSString *)transferId
{
    return [[KSTransferCenter shared] pauseWithId:transferId];
}

- (BOOL)resumeTransferWithId:(NSString *)transferId
{
    return [[KSTransferCenter shared] resumeWithId:transferId];
}

- (BOOL)cancelTransferWithId:(NSString *)transferId
{
    return [[KSTransferCenter shared] cancelWithId:transferId];
}

- (void)sendClipboardText:(NSString *)text
{
    [self.link sendMessage:@{ @"t": @"clip", @"mode": @"set", @"text": text ?: @"" }];
}

- (void)close
{
    [self.link sendMessage:@{ @"t": @"bye" }];
    [self.link close];
    self.link = nil;
}

#pragma mark 接收数据

- (void)beginReceivingTransferId:(NSString *)tid name:(NSString *)name size:(unsigned long long)size
{
    KSTransfer *tr = [self transferWithId:tid];
    if (!tr) {
        tr = [[KSTransfer alloc] init];
        tr.transferId = tid;
        tr.fileName = name;
        tr.peerName = self.peer.name;
        tr.direction = KSTransferDirectionDownload;
        [_transfers addObject:tr];
    }
    tr.total = size;
    tr.state = KSTransferStateRunning;
    tr.startedAt = [NSDate date];
    [[KSTransferCenter shared] track:tr];
    [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:tr];

    // 灵动岛：开始实时活动
    KSActivityStart([NSString stringWithFormat:@"下载 %@", name], self.peer.name ?: @"KeShare", size);

    if (self.peer.host.length == 0) {
        [self failTransferId:tid reason:@"对端地址未知"];
        return;
    }

    NSString *dir = KSConfig.shared.receivePath;
    [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES
                                                   attributes:nil error:NULL];
    NSString *dest = KSUniquePathInDirectory(dir, name);
    self.localDest[tid] = dest;
    tr.path = dest;

    __weak typeof(self) weakSelf = self;
    [KSDataReceiver receiveFromHost:self.peer.host
                               port:self.peer.port
                              token:self.token ?: @""
                         transferId:tid
                               size:size
                        destination:dest
                           transfer:tr
                         onProgress:^(unsigned long long done) {
        KSTransfer *t2 = [weakSelf transferWithId:tid];
        t2.done = done;
        if (t2.state != KSTransferStatePaused) t2.state = KSTransferStateRunning;
        KSActivityUpdate(t2.progress, [NSString stringWithFormat:@"%@ / %@",
                                       KSFormatBytes(done), KSFormatBytes(t2.total)]);
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:t2];
    } completion:^(NSString *tempPath, NSString *error) {
        KSTransfer *t2 = [weakSelf transferWithId:tid];
        if (error) {
            t2.state = KSTransferStateFailed;
            t2.errorText = error;
            t2.speedBps = 0;
            [[KSLog shared] error:@"下载 %@ 失败: %@", tid, error];
        } else {
            t2.state = KSTransferStateVerifying;
            t2.speedBps = 0;
            weakSelf.localTemp[tid] = tempPath;
            [[KSLog shared] info:@"下载 %@ 数据完成 %@，等待哈希", tid, tempPath.lastPathComponent];
        }
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:t2];
    }];
}

- (void)verifyIncomingHash:(NSDictionary *)msg
{
    NSString *tid = msg[@"id"];
    NSString *expect = msg[@"sha_ke_1024"] ?: msg[@"hash"];
    KSTransfer *tr = [self transferWithId:tid];
    NSString *path = self.localTemp[tid] ?: tr.path;

    if (path.length == 0 || ![[NSFileManager defaultManager] fileExistsAtPath:path]) {
        [self.link sendMessage:@{ @"t": @"hash_resp", @"id": tid ?: @"", @"ok": @NO, @"err": @"notfound" }];
        return;
    }
    NSString *actual = KSHashOfFile(path, NULL);
    BOOL ok = actual && [actual isEqualToString:expect];
    if (!ok) {
        [[NSFileManager defaultManager] removeItemAtPath:path error:NULL];
        tr.state = KSTransferStateFailed;
        tr.errorText = @"校验失败";
        [[KSLog shared] error:@"%@ 校验失败：期望 %@… 实际 %@…", tid,
            [expect substringToIndex:MIN(16u, expect.length)],
            [actual substringToIndex:MIN(16u, actual.length)]];
    } else {
        NSString *dest = self.localDest[tid];
        if (dest) {
            [[NSFileManager defaultManager] removeItemAtPath:dest error:NULL];
            [[NSFileManager defaultManager] moveItemAtPath:path toPath:dest error:NULL];
            path = dest;
        }
        tr.state = KSTransferStateDone;
        tr.verified = YES;
        tr.done = tr.total;
        tr.speedBps = 0;
        tr.path = path;
        [[KSLog shared] info:@"%@ 校验通过：%@", tid, path.lastPathComponent];
    }
    [self.localTemp removeObjectForKey:tid ?: @""];
    [self.localDest removeObjectForKey:tid ?: @""];
    [self.link sendMessage:@{ @"t": @"hash_resp", @"id": tid ?: @"", @"ok": @(ok) }];
    KSActivityFinish(ok, ok ? @"校验通过，传输完成" : @"校验失败，文件已删除");
    [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:tr];
}

#pragma mark 辅助

- (KSTransfer *)transferWithId:(NSString *)tid
{
    for (KSTransfer *t in self.transfers) if ([t.transferId isEqualToString:tid]) return t;
    return nil;
}

- (void)failTransferId:(NSString *)tid reason:(NSString *)reason
{
    KSTransfer *t = [self transferWithId:tid];
    if (t) {
        t.state = KSTransferStateFailed;
        t.errorText = reason;
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:t];
    }
}

- (unsigned long long)fileSize:(NSString *)path
{
    NSDictionary *a = [[NSFileManager defaultManager] attributesOfItemAtPath:path error:NULL];
    return a ? [a[NSFileSize] unsignedLongLongValue] : 0;
}

@end

#pragma mark - 数据接收端（连接发送方读取）

@implementation KSDataReceiver

+ (void)receiveFromHost:(NSString *)host
                   port:(uint16_t)port
                  token:(NSString *)token
             transferId:(NSString *)tid
                   size:(unsigned long long)size
            destination:(NSString *)dest
               transfer:(KSTransfer *)transfer
             onProgress:(void (^)(unsigned long long))progress
             completion:(void (^)(NSString *tempPath, NSString *error))completion
{
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) { completion(nil, @"socket 失败"); return; }
        struct sockaddr_in addr;
        memset(&addr, 0, sizeof addr);
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port ?: (uint16_t)KSConfig.shared.port);
        if (inet_pton(AF_INET, host.UTF8String, &addr.sin_addr) != 1) {
            close(fd);
            completion(nil, @"地址无效");
            return;
        }
        if (connect(fd, (struct sockaddr *)&addr, sizeof addr) != 0) {
            close(fd);
            completion(nil, @"连接失败");
            return;
        }
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        struct timeval tv = { 30, 0 };
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

        NSString *line = [NSString stringWithFormat:@"DATA %@ %@\n", token ?: @"-", tid];
        NSData *ld = [line dataUsingEncoding:NSUTF8StringEncoding];
        if (write(fd, ld.bytes, ld.length) != (ssize_t)ld.length) {
            close(fd);
            completion(nil, @"握手写入失败");
            return;
        }

        NSString *tmp = [NSTemporaryDirectory() stringByAppendingPathComponent:
                         [NSString stringWithFormat:@"ks-recv-%@.part", tid]];
        [[NSFileManager defaultManager] removeItemAtPath:tmp error:NULL];
        [[NSFileManager defaultManager] createFileAtPath:tmp contents:nil attributes:nil];
        NSFileHandle *fh = [NSFileHandle fileHandleForWritingAtPath:tmp];

        unsigned long long written = 0;
        BOOL ok = YES;
        uint32_t expectSeq = 0;
        NSTimeInterval lastT = [NSDate timeIntervalSinceReferenceDate];
        unsigned long long lastBytes = 0;

        while (YES) {
            uint8_t hdr[16];
            if (!KSReadFull(fd, hdr, 16)) { ok = NO; break; }
            uint32_t seq = ((uint32_t)hdr[0] << 24) | ((uint32_t)hdr[1] << 16) |
                           ((uint32_t)hdr[2] << 8) | hdr[3];
            unsigned long long off = 0;
            for (int k = 0; k < 8; k++) off = (off << 8) | hdr[4 + k];
            uint32_t len = ((uint32_t)hdr[12] << 24) | ((uint32_t)hdr[13] << 16) |
                           ((uint32_t)hdr[14] << 8) | hdr[15];

            if (seq != expectSeq || off != written) {
                [[KSLog shared] error:@"帧顺序错误 seq=%u/%u off=%llu/%llu", seq, expectSeq, off, written];
                ok = NO;
                break;
            }
            expectSeq++;
            if (len == 0) break;
            if (len > 4u * 1024 * 1024) { ok = NO; break; }

            NSMutableData *buf = [NSMutableData dataWithLength:len];
            if (!KSReadFull(fd, buf.mutableBytes, len)) { ok = NO; break; }
            [fh writeData:buf];
            written += len;

            if (transfer) {
                // 暂停 / 取消
                if (![transfer checkpoint]) {
                    [fh closeFile];
                    close(fd);
                    [[NSFileManager defaultManager] removeItemAtPath:tmp error:NULL];
                    completion(nil, @"已取消");
                    return;
                }
                NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
                if (now - lastT >= 0.4) {
                    double inst = (double)(written - lastBytes) / (now - lastT);
                    transfer.speedBps = (transfer.speedBps > 0)
                        ? (transfer.speedBps * 0.6 + inst * 0.4) : inst;
                    lastBytes = written;
                    lastT = now;
                }
            }
            if (progress) progress(written);
        }
        [fh closeFile];
        close(fd);

        if (!ok) {
            [[NSFileManager defaultManager] removeItemAtPath:tmp error:NULL];
            completion(nil, @"数据接收失败");
            return;
        }
        if (size && written != size) {
            [[NSFileManager defaultManager] removeItemAtPath:tmp error:NULL];
            completion(nil, [NSString stringWithFormat:@"长度不符 %llu/%llu", written, size]);
            return;
        }
        // 先落在临时文件，等 CTL 上的哈希校验通过后再改名
        completion(tmp, nil);
    });
}

@end


