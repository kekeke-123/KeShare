//
//  KSNetServe.m -- 作为“发送方”把文件写进数据连接 + get/put_offer 处理
//  KeShare 1.0.1
//
//  协议 §4.0：接收方主动连发送方，所以对端连进来时若本次传输登记了
//  servePath，就由本机写数据帧；否则才是接收。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <sys/socket.h>
#import <unistd.h>

@implementation KSServer (Serve)

#pragma mark - 小工具

/// 用控制链路里记录的远端地址去找设备名，找不到就给个通用名
static NSString *KSPeerNameForLink(id<KSControlLink> link)
{
    NSString *host = nil;
    if ([link conformsToProtocol:@protocol(KSServerControlLink)]) {
        host = ((id<KSServerControlLink>)link).remoteIp;
    }
    if (host.length) {
        for (KSDevice *d in [KSServer shared].peers) {
            if ([d.host isEqualToString:host] && d.name.length) return d.name;
        }
    }
    return @"对方设备";
}

#pragma mark - 写数据帧（本机是发送方）

- (void)serveTransfer:(KSPendingTransfer *)pt onSocket:(int)fd
{
    NSString *path = pt.servePath;
    NSFileHandle *fh = [NSFileHandle fileHandleForReadingAtPath:path];
    if (!fh) {
        [[KSLog shared] error:@"发送失败：打不开 %@", path];
        close(fd);
        return;
    }
    unsigned long long total = KSFileSize(path);
    NSInteger kb = KSConfig.shared.chunkKB;
    NSUInteger chunk = (NSUInteger)((kb > 0 ? kb : 1024) * 1024);
    uint32_t seq = 0;
    unsigned long long off = 0;
    unsigned long long sent = 0;
    NSTimeInterval t0 = [NSDate timeIntervalSinceReferenceDate];
    NSTimeInterval lastT = t0;
    unsigned long long lastBytes = 0;
    KSTransfer *pub = pt.publicTransfer;
    if (pub) {
        pub.total = total;
        pub.state = KSTransferStateRunning;
        if (!pub.startedAt) pub.startedAt = [NSDate date];
    }

    [[KSLog shared] info:@"开始发送 %@（%llu 字节）", path.lastPathComponent, total];

    while (off < total) {
        @autoreleasepool {
            // 详情页的暂停 / 取消在这里生效
            if (pub && ![pub checkpoint]) {
                [fh closeFile];
                close(fd);
                [[KSLog shared] info:@"发送 %@ 已被取消", path.lastPathComponent];
                return;
            }

            NSData *data = [fh readDataOfLength:chunk];
            if (data.length == 0) break;
            uint8_t hdr[16];
            hdr[0] = (uint8_t)(seq >> 24); hdr[1] = (uint8_t)(seq >> 16);
            hdr[2] = (uint8_t)(seq >> 8);  hdr[3] = (uint8_t)seq;
            for (int k = 0; k < 8; k++) hdr[4 + k] = (uint8_t)(off >> (56 - 8 * k));
            uint32_t len = (uint32_t)data.length;
            hdr[12] = (uint8_t)(len >> 24); hdr[13] = (uint8_t)(len >> 16);
            hdr[14] = (uint8_t)(len >> 8);  hdr[15] = (uint8_t)len;

            KSWriteFull(fd, hdr, sizeof hdr);
            KSWriteFull(fd, data.bytes, data.length);

            off += data.length;
            sent += data.length;
            seq++;

            // 限速
            NSInteger kbps = KSConfig.shared.maxSpeedKBps;
            if (kbps > 0) {
                double target = (double)sent / (kbps * 1024.0);
                double spent = [NSDate timeIntervalSinceReferenceDate] - t0;
                if (target > spent) [NSThread sleepForTimeInterval:(target - spent)];
            }
            // 进度 + 速度
            if (pub) {
                pub.done = off;
                pub.total = total;
                if (pub.state != KSTransferStatePaused) pub.state = KSTransferStateRunning;
                NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
                if (now - lastT >= 0.4) {
                    double inst = (double)(off - lastBytes) / (now - lastT);
                    pub.speedBps = (pub.speedBps > 0) ? (pub.speedBps * 0.6 + inst * 0.4) : inst;
                    lastBytes = off;
                    lastT = now;
                }
                KSTransfer *tr = pub;
                dispatch_async(dispatch_get_main_queue(), ^{
                    [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification
                                                                        object:tr];
                });
            }
        }
    }

    // 结束帧：len = 0，offset = 总长
    uint8_t end[16];
    end[0] = (uint8_t)(seq >> 24); end[1] = (uint8_t)(seq >> 16);
    end[2] = (uint8_t)(seq >> 8);  end[3] = (uint8_t)seq;
    for (int k = 0; k < 8; k++) end[4 + k] = (uint8_t)(total >> (56 - 8 * k));
    memset(end + 12, 0, 4);
    KSWriteFull(fd, end, sizeof end);

    [fh closeFile];
    close(fd);

    if (pub) {
        pub.state = KSTransferStateVerifying;
        pub.speedBps = 0;
    }

    // 数据发完，经**触发本次传输的那条控制链路**声明摘要
    NSString *hash = KSHashOfFile(path, NULL);
    pt.awaitingAck = YES;
    id<KSControlLink> link = pt.requestLink ?: self.controlLink;
    [link sendMessage:@{ @"t": @"hash",
                         @"id": pt.transferId ?: @"",
                         @"hash": hash ?: @"",
                         @"size": @(total) }];
    [[KSLog shared] info:@"%@ 数据已发出，等待对端校验", path.lastPathComponent];
}

#pragma mark - 控制通道：get / put_offer / hash_resp

- (void)handleGetRequest:(NSDictionary *)msg link:(id<KSControlLink>)link
{
    NSString *tid = msg[@"id"] ?: @"";
    NSString *rel = msg[@"path"] ?: @"";
    if (!KSIsValidRelativePath(rel)) {
        [link sendMessage:@{ @"t": @"get_resp", @"ok": @NO, @"id": tid, @"err": @"denied" }];
        return;
    }
    NSString *abs = [KSConfig.shared.shareRoot stringByAppendingPathComponent:rel];
    BOOL isDir = NO;
    if (![[NSFileManager defaultManager] fileExistsAtPath:abs isDirectory:&isDir] || isDir) {
        [link sendMessage:@{ @"t": @"get_resp", @"ok": @NO, @"id": tid, @"err": @"notfound" }];
        return;
    }
    unsigned long long size = KSFileSize(abs);
    NSInteger maxMB = KSConfig.shared.maxFileMB > 0 ? KSConfig.shared.maxFileMB : 2048;
    if (size > (unsigned long long)maxMB * 1024ULL * 1024ULL) {
        [link sendMessage:@{ @"t": @"get_resp", @"ok": @NO, @"id": tid, @"err": @"too_large" }];
        return;
    }

    KSPendingTransfer *pt = [[KSPendingTransfer alloc] init];
    pt.transferId = tid;
    pt.servePath = abs;
    pt.totalSize = size;
    pt.requestLink = link;
    [[KSDataRegistry shared] addPending:pt];

    KSTransfer *tr = [[KSTransfer alloc] init];
    tr.transferId = tid;
    tr.fileName = abs.lastPathComponent;
    tr.path = abs;
    tr.peerName = KSPeerNameForLink(link);
    tr.direction = KSTransferDirectionUpload;
    tr.total = size;
    tr.state = KSTransferStateWaiting;
    pt.publicTransfer = tr;
    [[KSTransferCenter shared] track:tr];

    [link sendMessage:@{ @"t": @"get_resp", @"ok": @YES, @"id": tid,
                         @"name": abs.lastPathComponent,
                         @"size": @(size) }];
    [[KSLog shared] info:@"对端请求下载 %@（%llu 字节）", rel, size];
}

- (void)handleListRequest:(NSDictionary *)msg link:(id<KSControlLink>)link
{
    NSString *rel = msg[@"path"] ?: @"";
    if (rel.length && !KSIsValidRelativePath(rel)) {
        [link sendMessage:@{ @"t": @"list_resp", @"ok": @NO, @"files": @[], @"err": @"denied" }];
        return;
    }
    NSString *root = KSConfig.shared.shareRoot;
    NSString *dir = rel.length ? [root stringByAppendingPathComponent:rel] : root;
    BOOL isDir = NO;
    if (![[NSFileManager defaultManager] fileExistsAtPath:dir isDirectory:&isDir] || !isDir) {
        [link sendMessage:@{ @"t": @"list_resp", @"ok": @NO, @"files": @[], @"err": @"notfound" }];
        return;
    }
    NSArray *names = [[NSFileManager defaultManager] contentsOfDirectoryAtPath:dir error:NULL] ?: @[];
    names = [names sortedArrayUsingSelector:@selector(localizedStandardCompare:)];
    NSMutableArray *files = [NSMutableArray array];
    for (NSString *name in names) {
        NSString *full = [dir stringByAppendingPathComponent:name];
        NSDictionary *attr = [[NSFileManager defaultManager] attributesOfItemAtPath:full error:NULL];
        BOOL childDir = [attr[NSFileType] isEqual:NSFileTypeDirectory];
        NSMutableDictionary *entry = [@{
            @"path": rel.length ? [rel stringByAppendingPathComponent:name] : name,
            @"name": name,
            @"kind": childDir ? @"dir" : @"file",
            @"mtime": @((long long)[attr[NSFileModificationDate] timeIntervalSince1970])
        } mutableCopy];
        if (!childDir) entry[@"size"] = @([attr[NSFileSize] unsignedLongLongValue]);
        [files addObject:entry];
    }
    [link sendMessage:@{ @"t": @"list_resp", @"ok": @YES, @"files": files }];
    [[KSLog shared] info:@"列出共享目录 %@：%lu 项", rel.length ? rel : @"/", (unsigned long)files.count];
}

- (void)handlePutOffer:(NSDictionary *)msg link:(id<KSControlLink>)link
{
    NSString *tid = msg[@"id"] ?: @"";
    NSString *name = msg[@"name"] ?: @"file";
    unsigned long long size = [msg[@"size"] unsignedLongLongValue];
    NSInteger maxMB = KSConfig.shared.maxFileMB > 0 ? KSConfig.shared.maxFileMB : 2048;

    if (!KSConfig.shared.allowUpload) {
        [link sendMessage:@{ @"t": @"put_resp", @"ok": @NO, @"id": tid, @"err": @"denied" }];
        return;
    }
    if (size > (unsigned long long)maxMB * 1024ULL * 1024ULL) {
        [link sendMessage:@{ @"t": @"put_resp", @"ok": @NO, @"id": tid, @"err": @"too_large" }];
        return;
    }
    if (!KSIsValidRelativePath(name)) {
        [link sendMessage:@{ @"t": @"put_resp", @"ok": @NO, @"id": tid, @"err": @"denied" }];
        return;
    }

    [[NSFileManager defaultManager] createDirectoryAtPath:KSConfig.shared.receivePath
                              withIntermediateDirectories:YES attributes:nil error:NULL];

    KSPendingTransfer *pt = [[KSPendingTransfer alloc] init];
    pt.transferId = tid;
    pt.totalSize = size;
    pt.requestLink = link;
    // 不覆盖同名已接收文件
    pt.finalPath = KSUniquePathInDirectory(KSConfig.shared.receivePath, name);
    [[KSDataRegistry shared] addPending:pt];

    NSString *peerName = KSPeerNameForLink(link);
    KSTransfer *tr = [[KSTransfer alloc] init];
    tr.transferId = tid;
    tr.fileName = pt.finalPath.lastPathComponent;
    tr.peerName = peerName;
    tr.direction = KSTransferDirectionDownload;
    tr.total = size;
    tr.state = KSTransferStateWaiting;
    tr.path = pt.finalPath;
    pt.publicTransfer = tr;
    [[KSTransferCenter shared] track:tr];

    [link sendMessage:@{ @"t": @"put_resp", @"ok": @YES, @"id": tid }];
    [[KSLog shared] info:@"对端要推送 %@（%llu 字节）", name, size];

    // 协议 §4.0：接收方主动发起数据连接。
    // 对端地址优先取 put_offer 里带的 host/port，否则回退到已知设备表。
    NSString *host = msg[@"host"];
    uint16_t port = (uint16_t)[msg[@"port"] unsignedShortValue];
    if (host.length == 0 || port == 0) {
        NSString *did = msg[@"did"] ?: @"";
        for (KSDevice *d in [KSServer shared].peers) {
            if (did.length == 0 || [d.deviceId isEqualToString:did]) {
                host = d.host;
                port = d.port;
                break;
            }
        }
    }
    if (host.length == 0 || port == 0) {
        [[KSLog shared] warn:@"推送 %@ 缺少对端地址，无法接收", tid];
        tr.state = KSTransferStateFailed;
        tr.errorText = @"对端地址未知";
        [[KSTransferCenter shared] track:tr];
        return;
    }

    tr.state = KSTransferStateRunning;
    tr.startedAt = [NSDate date];
    NSString *dest = pt.finalPath;
    NSString *dataToken = msg[@"token"] ?: @"";
    [KSDataReceiver receiveFromHost:host port:port token:dataToken
                         transferId:tid size:size destination:dest transfer:tr
                         onProgress:^(unsigned long long done) {
        KSTransfer *pub = [[KSTransferCenter shared] transferWithId:tid] ?: tr;
        pub.done = done;
        if (pub.state != KSTransferStatePaused) pub.state = KSTransferStateRunning;
        dispatch_async(dispatch_get_main_queue(), ^{
            [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
        });
    } completion:^(NSString *tempPath, NSString *error) {
        KSTransfer *pub = [[KSTransferCenter shared] transferWithId:tid] ?: tr;
        if (error) {
            pub.state = KSTransferStateFailed;
            pub.errorText = error;
            pub.speedBps = 0;
            [[KSLog shared] error:@"接收 %@ 失败: %@", tid, error];
        } else {
            pub.state = KSTransferStateVerifying;
            pub.speedBps = 0;
            // hash 校验靠 pt.tempPath 找文件（见 handleHashMessage:）
            if (tempPath.length) pt.tempPath = tempPath;
            [[KSLog shared] info:@"接收 %@ 数据完成 %@，等待哈希", tid, tempPath.lastPathComponent];
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
        });
    }];
}

- (void)handleHashAck:(NSDictionary *)msg
{
    NSString *tid = msg[@"id"] ?: @"";
    BOOL ok = [msg[@"ok"] boolValue];
    KSPendingTransfer *pt = [[KSDataRegistry shared] pendingForId:tid];
    KSTransfer *pub = pt.publicTransfer ?: [[KSTransferCenter shared] transferWithId:tid];
    if (pub) {
        pub.state = ok ? KSTransferStateDone : KSTransferStateFailed;
        pub.verified = ok;
        pub.speedBps = 0;
        if (!ok) pub.errorText = msg[@"err"] ?: @"对端校验失败";
        dispatch_async(dispatch_get_main_queue(), ^{
            [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
        });
    }
    [[KSLog shared] info:@"传输 %@ %@", tid, ok ? @"对端校验通过" : @"对端校验失败"];
    [[KSDataRegistry shared] removePendingForId:tid];
}

@end
