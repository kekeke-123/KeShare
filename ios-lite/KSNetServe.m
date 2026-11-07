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
    NSUInteger chunk = (NSUInteger)((kb > 0 ? kb : 64) * 1024);
    uint32_t seq = 0;
    unsigned long long off = 0;
    unsigned long long sent = 0;
    NSTimeInterval t0 = [NSDate timeIntervalSinceReferenceDate];

    [[KSLog shared] info:@"开始发送 %@（%llu 字节）", path.lastPathComponent, total];

    while (off < total) {
        @autoreleasepool {
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
            // 进度
            if (pt.publicTransfer) {
                pt.publicTransfer.done = off;
                pt.publicTransfer.total = total;
                pt.publicTransfer.state = KSTransferStateRunning;
                KSTransfer *tr = pt.publicTransfer;
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

    // 数据发完，经控制通道声明摘要
    NSString *hash = KSHashOfFile(path, NULL);
    pt.awaitingAck = YES;
    [self.controlLink sendMessage:@{ @"t": @"hash",
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
    KSPendingTransfer *pt = [[KSPendingTransfer alloc] init];
    pt.transferId = tid;
    pt.servePath = abs;
    pt.totalSize = KSFileSize(abs);
    [[KSDataRegistry shared] addPending:pt];

    [link sendMessage:@{ @"t": @"get_resp", @"ok": @YES, @"id": tid,
                         @"name": abs.lastPathComponent,
                         @"size": @(pt.totalSize) }];
    [[KSLog shared] info:@"对端请求下载 %@（%llu 字节）", rel, pt.totalSize];
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

    KSPendingTransfer *pt = [[KSPendingTransfer alloc] init];
    pt.transferId = tid;
    pt.totalSize = size;
    pt.finalPath = [KSConfig.shared.receivePath stringByAppendingPathComponent:name];
    [[KSDataRegistry shared] addPending:pt];
    [[NSFileManager defaultManager] createDirectoryAtPath:KSConfig.shared.receivePath
                              withIntermediateDirectories:YES attributes:nil error:NULL];

    [link sendMessage:@{ @"t": @"put_resp", @"ok": @YES, @"id": tid }];
    [[KSLog shared] info:@"对端要推送 %@（%llu 字节）", name, size];
}

- (void)handleHashAck:(NSDictionary *)msg
{
    NSString *tid = msg[@"id"] ?: @"";
    BOOL ok = [msg[@"ok"] boolValue];
    KSPendingTransfer *pt = [[KSDataRegistry shared] pendingForId:tid];
    KSTransfer *pub = pt.publicTransfer;
    if (pub) {
        pub.state = ok ? KSTransferStateDone : KSTransferStateFailed;
        if (!ok) pub.errorText = @"对端校验失败";
        dispatch_async(dispatch_get_main_queue(), ^{
            [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification object:pub];
        });
    }
    [[KSLog shared] info:@"传输 %@ %@", tid, ok ? @"对端校验通过" : @"对端校验失败"];
    [[KSDataRegistry shared] removePendingForId:tid];
}

@end
