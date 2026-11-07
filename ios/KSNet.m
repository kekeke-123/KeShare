//
//  KSNet.m -- 控制链路（蓝牙 / WiFi 回退）、数据通道（WiFi 分块）、发现、会话
//  KeShare 1.0
//
//  控制通道：BLE GATT（RX 写入 / TX 通知），一帧 = 4 字节大端长度 + JSON；
//            超过 180 字节按 3 字节分片头（idx:u16, more:u8）切分。
//  WiFi 回退：连接对端 47377，发 "CTL <token>\n" 后改用同样的 4 字节长度帧。
//  数据通道：TCP 47377，首行 "DATA <token> <id>\n"，随后
//            seq:u32 | offset:u64 | len:u32 | payload。
//  完整性：传输结束后由控制通道交换 SHA-KE-1024 摘要并校验。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <CoreBluetooth/CoreBluetooth.h>
#import <arpa/inet.h>
#import <netinet/in.h>
#import <netinet/tcp.h>
#import <sys/socket.h>
#import <sys/time.h>
#import <unistd.h>
#import <fcntl.h>
#import <ifaddrs.h>

#pragma mark - 小工具

#pragma mark - 设备

@implementation KSDevice

+ (instancetype)deviceWithDictionary:(NSDictionary *)dict
{
    KSDevice *d = [[KSDevice alloc] init];
    d.deviceId = dict[@"did"] ?: @"";
    d.name = dict[@"name"] ?: @"未知设备";
    d.osVersion = dict[@"osv"] ?: @"";
    d.port = (uint16_t)[dict[@"port"] unsignedShortValue];
    d.caps = [dict[@"caps"] unsignedIntValue];
    NSString *kind = dict[@"kind"] ?: @"";
    if ([kind isEqualToString:@"ios"]) d.kind = KSDeviceKindIOS;
    else if ([kind isEqualToString:@"windows"]) d.kind = KSDeviceKindWindows;
    else if ([kind isEqualToString:@"mac"]) d.kind = KSDeviceKindMac;
    else if ([kind isEqualToString:@"android"]) d.kind = KSDeviceKindAndroid;
    else d.kind = KSDeviceKindUnknown;
    return d;
}

- (NSString *)kindLabel
{
    switch (self.kind) {
        case KSDeviceKindIOS: return @"iOS";
        case KSDeviceKindWindows: return @"Windows";
        case KSDeviceKindMac: return @"macOS";
        case KSDeviceKindAndroid: return @"Android";
        default: return @"设备";
    }
}

@end

#pragma mark - 传输任务

@implementation KSTransfer
{
    NSCondition *_gate;
    BOOL _paused;
    BOOL _cancelled;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _gate = [[NSCondition alloc] init];
        _paused = NO;
        _cancelled = NO;
        _startedAt = [NSDate date];
    }
    return self;
}

- (double)progress
{
    if (self.total == 0) return self.state == KSTransferStateDone ? 1.0 : 0.0;
    return MIN(1.0, (double)self.done / (double)self.total);
}

- (NSString *)stateLabel
{
    switch (self.state) {
        case KSTransferStateWaiting: return @"等待";
        case KSTransferStateRunning: return @"传输中";
        case KSTransferStatePaused: return @"已暂停";
        case KSTransferStateVerifying: return @"校验中";
        case KSTransferStateDone: return self.verified ? @"完成（已校验）" : @"完成";
        case KSTransferStateFailed: return self.errorText ?: @"失败";
    }
    return @"";
}

- (NSString *)speedLabel
{
    if (self.state == KSTransferStateDone) return @"—";
    if (self.state == KSTransferStatePaused) return @"已暂停";
    if (self.speedBps <= 1) return @"—";
    return [NSString stringWithFormat:@"%@/s", KSFormatBytes((unsigned long long)self.speedBps)];
}

- (BOOL)paused
{
    [_gate lock];
    BOOL v = _paused;
    [_gate unlock];
    return v;
}

- (BOOL)cancelled
{
    [_gate lock];
    BOOL v = _cancelled;
    [_gate unlock];
    return v;
}

- (void)pause
{
    [_gate lock];
    if (!_cancelled && self.state != KSTransferStateDone && self.state != KSTransferStateFailed) {
        _paused = YES;
        self.state = KSTransferStatePaused;
        self.speedBps = 0;
        [_gate broadcast];
    }
    [_gate unlock];
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification
                                                            object:self];
    });
}

- (void)resume
{
    [_gate lock];
    if (_paused) {
        _paused = NO;
        if (self.state == KSTransferStatePaused) self.state = KSTransferStateRunning;
        [_gate broadcast];
    }
    [_gate unlock];
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification
                                                            object:self];
    });
}

- (void)cancel
{
    [_gate lock];
    _cancelled = YES;
    _paused = NO;
    self.state = KSTransferStateFailed;
    self.errorText = @"已取消";
    self.speedBps = 0;
    [_gate broadcast];
    [_gate unlock];
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification
                                                            object:self];
    });
}

- (BOOL)checkpoint
{
    BOOL go = YES;
    [_gate lock];
    while (_paused && !_cancelled) {
        [_gate waitUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
    }
    go = !_cancelled;
    [_gate unlock];
    return go;
}

@end

#pragma mark - 全局传输记录

@implementation KSTransferCenter
{
    NSMutableArray<KSTransfer *> *_items;
    dispatch_queue_t _q;
}

+ (KSTransferCenter *)shared
{
    static KSTransferCenter *c;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ c = [[KSTransferCenter alloc] init]; });
    return c;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _items = [NSMutableArray array];
        _q = dispatch_queue_create("keshare.transfers", DISPATCH_QUEUE_SERIAL);
    }
    return self;
}

- (NSArray<KSTransfer *> *)transfers
{
    __block NSArray *snapshot;
    dispatch_sync(_q, ^{ snapshot = [self->_items copy]; });
    return snapshot;
}

- (void)track:(KSTransfer *)transfer
{
    if (!transfer.transferId.length) return;
    dispatch_sync(_q, ^{
        BOOL replaced = NO;
        for (NSUInteger i = 0; i < self->_items.count; i++) {
            if ([self->_items[i].transferId isEqualToString:transfer.transferId]) {
                self->_items[i] = transfer;
                replaced = YES;
                break;
            }
        }
        if (!replaced) [self->_items insertObject:transfer atIndex:0];
        // 只保留最近 60 条已结束的记录，避免无限增长
        if (self->_items.count > 60) {
            NSMutableArray *keep = [NSMutableArray array];
            NSInteger finished = 0;
            for (KSTransfer *t in self->_items) {
                BOOL done = (t.state == KSTransferStateDone ||
                             t.state == KSTransferStateFailed);
                if (done) finished++;
                if (done && finished > 40) continue;
                [keep addObject:t];
            }
            if (keep.count < self->_items.count) self->_items = keep;
        }
    });
    if (!transfer.startedAt) transfer.startedAt = [NSDate date];
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification
                                                            object:transfer];
    });
}

- (KSTransfer *)transferWithId:(NSString *)tid
{
    if (!tid.length) return nil;
    __block KSTransfer *found;
    dispatch_sync(_q, ^{
        for (KSTransfer *t in self->_items) {
            if ([t.transferId isEqualToString:tid]) { found = t; break; }
        }
    });
    return found;
}

- (BOOL)pauseWithId:(NSString *)tid
{
    KSTransfer *t = [self transferWithId:tid];
    if (!t || t.state == KSTransferStateDone || t.state == KSTransferStateFailed) return NO;
    [t pause];
    [[KSLog shared] info:@"传输 %@ 已暂停", tid];
    return YES;
}

- (BOOL)resumeWithId:(NSString *)tid
{
    KSTransfer *t = [self transferWithId:tid];
    if (!t || !t.paused) return NO;
    [t resume];
    [[KSLog shared] info:@"传输 %@ 继续", tid];
    return YES;
}

- (BOOL)cancelWithId:(NSString *)tid
{
    KSTransfer *t = [self transferWithId:tid];
    if (!t) return NO;
    [t cancel];
    [[KSLog shared] info:@"传输 %@ 已取消", tid];
    return YES;
}

- (void)clearFinished
{
    dispatch_sync(_q, ^{
        NSMutableArray *keep = [NSMutableArray array];
        for (KSTransfer *t in self->_items) {
            if (t.state != KSTransferStateDone && t.state != KSTransferStateFailed) {
                [keep addObject:t];
            }
        }
        self->_items = keep;
    });
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] postNotificationName:KSTransfersDidChangeNotification
                                                            object:nil];
    });
}

@end

#pragma mark - 蓝牙控制链路

@interface KSBleLink : NSObject <KSControlLink, CBPeripheralManagerDelegate, CBCentralManagerDelegate, CBPeripheralDelegate>
@property (nonatomic, weak) id<KSControlLinkDelegate> delegate;
@property (nonatomic, copy) void (^onPeerFound)(KSDevice *peer, id peripheralOrIdentifier);
@property (nonatomic, readonly) BOOL isOpen;
@property (nonatomic, readonly) NSString *transportName;
@property (nonatomic, readonly) BOOL available;
- (void)startAdvertising;
- (void)stopAdvertising;
- (void)startScanning;
- (void)stopScanning;
- (BOOL)connectPeripheral:(id)peripheral;
@end

@implementation KSBleLink
{
    CBPeripheralManager *_pm;
    CBMutableCharacteristic *_rx;      // 对端写入
    CBMutableCharacteristic *_tx;      // 本机通知
    CBCentralManager *_cm;
    CBPeripheral *_peripheral;
    CBCharacteristic *_writeChar;      // 对端 RX
    CBCharacteristic *_notifyChar;     // 对端 TX

    NSMutableData *_rxBuf;
    NSMutableData *_txQueue;
    NSMutableDictionary<NSNumber *, NSData *> *_fragments;
    NSUInteger _fragTotal;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _rxBuf = [NSMutableData data];
        _txQueue = [NSMutableData data];
        _fragments = [NSMutableDictionary dictionary];
        _available = YES;
    }
    return self;
}

- (NSString *)transportName { return @"蓝牙"; }
- (BOOL)isOpen { return _writeChar != nil || (_pm && _pm.isAdvertising && _rx); }

- (void)startAdvertising
{
    if (_pm) return;
    _pm = [[CBPeripheralManager alloc] initWithDelegate:self queue:dispatch_get_main_queue()];
}

- (void)stopAdvertising
{
    if (_pm) [_pm stopAdvertising];
}

- (void)startScanning
{
    if (_cm) return;
    _cm = [[CBCentralManager alloc] initWithDelegate:self queue:dispatch_get_main_queue()];
}

- (void)stopScanning
{
    if (_cm) [_cm stopScan];
}

- (void)peripheralManagerDidUpdateState:(CBPeripheralManager *)peripheral
{
    if (peripheral.state != CBManagerStatePoweredOn) {
        [[KSLog shared] warn:@"蓝牙不可用（peripheral state=%ld），将使用 WiFi 回退",
            (long)peripheral.state];
        return;
    }
    CBUUID *svc = [CBUUID UUIDWithString:KS_SERVICE_UUID];
    _rx = [[CBMutableCharacteristic alloc] initWithType:[CBUUID UUIDWithString:KS_CHAR_RX_UUID]
                                             properties:CBCharacteristicPropertyWrite | CBCharacteristicPropertyWriteWithoutResponse
                                                  value:nil permissions:CBAttributePermissionsWriteable];
    _tx = [[CBMutableCharacteristic alloc] initWithType:[CBUUID UUIDWithString:KS_CHAR_TX_UUID]
                                             properties:CBCharacteristicPropertyNotify
                                                  value:nil permissions:CBAttributePermissionsReadable];
    CBMutableService *service = [[CBMutableService alloc] initWithType:svc primary:YES];
    service.characteristics = @[_rx, _tx];
    [_pm addService:service];
}

- (void)peripheralManager:(CBPeripheralManager *)peripheral didAddService:(CBService *)service error:(NSError *)error
{
    if (error) { [[KSLog shared] error:@"注册 GATT 服务失败: %@", error.localizedDescription]; return; }
    // 把 IP:端口 编进广播名，Windows 端靠 BLE 就能拿到地址（UDP 被防火墙拦时唯一通路）
    // 格式：K|<ip>:<port>|<did后6位>，总长控制在 31 字节内
    NSString *ip = KSIPv4StringLocal();
    NSString *tail = KSConfig.shared.deviceId.length >= 6
        ? [KSConfig.shared.deviceId substringFromIndex:KSConfig.shared.deviceId.length - 6] : @"";
    NSString *advName = [NSString stringWithFormat:@"K|%@|%@", ip, tail];

    [_pm startAdvertising:@{
        CBAdvertisementDataLocalNameKey: advName,
        CBAdvertisementDataServiceUUIDsKey: @[[CBUUID UUIDWithString:KS_SERVICE_UUID]]
    }];
    [[KSLog shared] info:@"蓝牙广播已启动：%@（%@）", advName, KSConfig.shared.deviceName];
}

- (void)peripheralManager:(CBPeripheralManager *)peripheral
    didReceiveWriteRequests:(NSArray<CBATTRequest *> *)requests
{
    for (CBATTRequest *req in requests) {
        if ([req.characteristic.UUID isEqual:[CBUUID UUIDWithString:KS_CHAR_RX_UUID]] && req.value) {
            [self consumeChunk:req.value];
        }
    }
}

- (void)centralManagerDidUpdateState:(CBCentralManager *)central
{
    if (central.state != CBManagerStatePoweredOn) {
        [[KSLog shared] warn:@"蓝牙扫描不可用（state=%ld）", (long)central.state];
        return;
    }
    [_cm scanForPeripheralsWithServices:@[[CBUUID UUIDWithString:KS_SERVICE_UUID]]
                                options:@{CBCentralManagerScanOptionAllowDuplicatesKey: @NO}];
}

- (void)centralManager:(CBCentralManager *)central
 didDiscoverPeripheral:(CBPeripheral *)peripheral
     advertisementData:(NSDictionary<NSString *, id> *)adv
                  RSSI:(NSNumber *)RSSI
{
    NSString *name = adv[CBAdvertisementDataLocalNameKey] ?: peripheral.name ?: @"KeShare";
    if (self.onPeerFound) {
        KSDevice *d = [[KSDevice alloc] init];
        d.name = name;
        d.kind = KSDeviceKindUnknown;
        d.deviceId = [NSString stringWithFormat:@"ble-%@", peripheral.identifier.UUIDString];
        d.host = KSIPv4StringLocal();
        d.port = (uint16_t)KSConfig.shared.port;
        self.onPeerFound(d, peripheral);
    }
}

- (BOOL)connectPeripheral:(id)peripheralArg
{
    CBPeripheral *peripheral = (CBPeripheral *)peripheralArg;
    if (!_cm || peripheral.state == CBPeripheralStateDisconnected) {
        _peripheral = peripheral;
        peripheral.delegate = self;
        [_cm connectPeripheral:peripheral options:@{}];
        return YES;
    }
    return NO;
}

- (void)centralManager:(CBCentralManager *)central didConnectPeripheral:(CBPeripheral *)peripheral
{
    [[KSLog shared] info:@"蓝牙已连接 %@", peripheral.name ?: peripheral.identifier.UUIDString];
    [peripheral discoverServices:@[[CBUUID UUIDWithString:KS_SERVICE_UUID]]];
}

- (void)centralManager:(CBCentralManager *)central didDisconnectPeripheral:(CBPeripheral *)peripheral error:(NSError *)error
{
    _writeChar = nil;
    _notifyChar = nil;
    [[KSLog shared] warn:@"蓝牙断开 %@", error.localizedDescription ?: @""];
    dispatch_async(dispatch_get_main_queue(), ^{
        if ([self.delegate respondsToSelector:@selector(controlLinkDidClose:)]) {
            [self.delegate controlLinkDidClose:self];
        }
    });
}

- (void)peripheral:(CBPeripheral *)peripheral didDiscoverServices:(NSError *)error
{
    for (CBService *s in peripheral.services) {
        [peripheral discoverCharacteristics:@[[CBUUID UUIDWithString:KS_CHAR_RX_UUID],
                                              [CBUUID UUIDWithString:KS_CHAR_TX_UUID]]
                                 forService:s];
    }
}

- (void)peripheral:(CBPeripheral *)peripheral
 didDiscoverCharacteristicsForService:(CBService *)service error:(NSError *)error
{
    for (CBCharacteristic *c in service.characteristics) {
        if ([c.UUID isEqual:[CBUUID UUIDWithString:KS_CHAR_RX_UUID]]) _writeChar = c;
        if ([c.UUID isEqual:[CBUUID UUIDWithString:KS_CHAR_TX_UUID]]) {
            _notifyChar = c;
            [peripheral setNotifyValue:YES forCharacteristic:c];
        }
    }
    if (_writeChar) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if ([self.delegate respondsToSelector:@selector(controlLinkDidOpen:)]) {
                [self.delegate controlLinkDidOpen:self];
            }
        });
    }
}

- (void)peripheral:(CBPeripheral *)peripheral
 didUpdateValueForCharacteristic:(CBCharacteristic *)characteristic error:(NSError *)error
{
    if (characteristic.value) [self consumeChunk:characteristic.value];
}

#pragma mark 分片

// 分片头：idx:u16 大端 | more:u8
- (void)consumeChunk:(NSData *)chunk
{
    if (chunk.length < 3) return;
    const uint8_t *b = chunk.bytes;
    uint16_t idx = (uint16_t)((b[0] << 8) | b[1]);
    uint8_t more = b[2];
    NSData *payload = [chunk subdataWithRange:NSMakeRange(3, chunk.length - 3)];
    _fragments[@(idx)] = payload;
    if (!more) {
        NSMutableData *full = [NSMutableData data];
        NSUInteger n = _fragments.count;
        for (NSUInteger i = 0; i < n; i++) {
            NSData *p = _fragments[@(i)];
            if (!p) { [_fragments removeAllObjects]; return; }
            [full appendData:p];
        }
        [_fragments removeAllObjects];
        [self handleFrame:full];
    }
}

- (void)handleFrame:(NSData *)frame
{
    if (frame.length < 4) return;
    const uint8_t *b = frame.bytes;
    uint32_t len = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | b[3];
    if (len + 4 > frame.length) return;
    NSData *json = [frame subdataWithRange:NSMakeRange(4, len)];
    id obj = [NSJSONSerialization JSONObjectWithData:json options:0 error:NULL];
    if (![obj isKindOfClass:[NSDictionary class]]) return;
    dispatch_async(dispatch_get_main_queue(), ^{
        if ([self.delegate respondsToSelector:@selector(controlLink:didReceiveMessage:)]) {
            [self.delegate controlLink:self didReceiveMessage:obj];
        }
    });
}

- (void)sendMessage:(NSDictionary *)message
{
    NSData *json = [NSJSONSerialization dataWithJSONObject:message options:0 error:NULL];
    if (!json) return;
    NSMutableData *frame = [NSMutableData data];
    uint32_t len = (uint32_t)json.length;
    uint8_t hdr[4] = { (uint8_t)(len >> 24), (uint8_t)(len >> 16), (uint8_t)(len >> 8), (uint8_t)len };
    [frame appendBytes:hdr length:4];
    [frame appendData:json];

    NSUInteger chunkSize = 180;
    NSUInteger total = (frame.length + chunkSize - 1) / chunkSize;
    for (NSUInteger i = 0; i < total; i++) {
        NSRange r = NSMakeRange(i * chunkSize, MIN(chunkSize, frame.length - i * chunkSize));
        NSMutableData *piece = [NSMutableData data];
        uint8_t head[3] = { (uint8_t)(i >> 8), (uint8_t)i, (uint8_t)(i + 1 < total) };
        [piece appendBytes:head length:3];
        [piece appendData:[frame subdataWithRange:r]];
        if (_writeChar && _peripheral) {
            [_peripheral writeValue:piece forCharacteristic:_writeChar
                              type:CBCharacteristicWriteWithoutResponse];
        } else if (_pm && _tx) {
            [_pm updateValue:piece forCharacteristic:_tx onSubscribedCentrals:nil];
        }
    }
    if (KSConfig.shared.verboseLogging) {
        [[KSLog shared] log:@"CTL→ %@", message[@"t"]];
    }
}

- (void)close
{
    if (_peripheral) [_cm cancelPeripheralConnection:_peripheral];
    _writeChar = nil;
    _notifyChar = nil;
    if (_pm) [_pm stopAdvertising];
}

@end



