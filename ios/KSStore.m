//
//  KSStore.m -- 配置(settings.json)与日志
//  KeShare 1.0
//

#import "KeShare.h"
#import "KSInternal.h"
#import <Security/Security.h>

NSNotificationName const KSConfigDidChangeNotification = @"KSConfigDidChange";
NSNotificationName const KSLogDidChangeNotification    = @"KSLogDidChange";
NSNotificationName const KSPeersDidChangeNotification  = @"KSPeersDidChange";
NSNotificationName const KSTransfersDidChangeNotification = @"KSTransfersDidChange";

#pragma mark - 工具

NSString *KSRandomHex(NSUInteger bytes)
{
    NSMutableString *s = [NSMutableString stringWithCapacity:bytes * 2];
    uint8_t *buf = malloc(bytes);
    if (!buf) return @"0";
    if (SecRandomCopyBytes(kSecRandomDefault, bytes, buf) != errSecSuccess) {
        arc4random_buf(buf, bytes);
    }
    for (NSUInteger i = 0; i < bytes; i++) [s appendFormat:@"%02x", buf[i]];
    free(buf);
    return s;
}

unsigned long long KSFileSize(NSString *path)
{
    NSDictionary *a = [[NSFileManager defaultManager] attributesOfItemAtPath:path error:NULL];
    return a ? [a[NSFileSize] unsignedLongLongValue] : 0;
}

NSString *KSFormatBytes(unsigned long long v)
{
    if (v < 1024) return [NSString stringWithFormat:@"%llu B", v];
    if (v < 1024ULL * 1024) return [NSString stringWithFormat:@"%.1f KB", v / 1024.0];
    if (v < 1024ULL * 1024 * 1024) return [NSString stringWithFormat:@"%.1f MB", v / (1024.0 * 1024.0)];
    return [NSString stringWithFormat:@"%.2f GB", v / (1024.0 * 1024.0 * 1024.0)];
}

BOOL KSIsValidRelativePath(NSString *path)
{
    if (path.length == 0 || path.length > 1024) return NO;
    if ([path hasPrefix:@"/"] || [path containsString:@":"]) return NO;
    if ([path containsString:@".."]) return NO;
    for (NSUInteger i = 0; i < path.length; i++) {
        unichar c = [path characterAtIndex:i];
        if (c < 0x20) return NO;
    }
    static NSSet *reserved = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        reserved = [NSSet setWithArray:@[@"con", @"nul", @"aux", @"prn",
                                         @"com1", @"com2", @"com3", @"com4", @"com5",
                                         @"com6", @"com7", @"com8", @"com9",
                                         @"lpt1", @"lpt2", @"lpt3", @"lpt4", @"lpt5",
                                         @"lpt6", @"lpt7", @"lpt8", @"lpt9"]];
    });
    for (NSString *comp in [path componentsSeparatedByString:@"/"]) {
        if (comp.length == 0) return NO;
        NSString *base = [[comp componentsSeparatedByString:@"."].firstObject lowercaseString];
        if ([reserved containsObject:base]) return NO;
    }
    return YES;
}

NSString *KSSHA256OfFile(NSString *path, NSError **error)
{
    // 由 KSStore.m 末尾的 SHA-KE-1024 实现替代（保留函数名以兼容调用点）
    return KSHashOfFile(path, error);
}

#pragma mark - SHA-KE-1024

NSString *KSHashOfFile(NSString *path, NSError **error)
{
    // 完整性摘要：SHA-256（CommonCrypto），64 个十六进制字符
    return KSSHA256HexOfFile(path, error);
}

NSString *KSHashOfData(NSData *data)
{
    return KSSHA256HexOfData(data ?: [NSData data]);
}
#pragma mark - 配置

@interface KSConfig ()
@property (nonatomic, strong) NSMutableDictionary *root;
@end

@implementation KSConfig

+ (KSConfig *)shared
{
    static KSConfig *c;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ c = [[KSConfig alloc] init]; [c reload]; });
    return c;
}

- (instancetype)init
{
    if ((self = [super init])) { _root = [NSMutableDictionary dictionary]; }
    return self;
}

- (NSURL *)configFileURL
{
    // 放在 Application Support：不会被“文件”App 看到
    NSURL *base = [[[NSFileManager defaultManager] URLsForDirectory:NSApplicationSupportDirectory
                                                          inDomains:NSUserDomainMask] firstObject];
    NSURL *dir = [base URLByAppendingPathComponent:@"KeShare" isDirectory:YES];
    [[NSFileManager defaultManager] createDirectoryAtURL:dir
                             withIntermediateDirectories:YES attributes:nil error:NULL];
    return [dir URLByAppendingPathComponent:@"settings.json"];
}

- (NSString *)configFilePath { return self.configFileURL.path; }

- (NSDictionary *)defaults
{
    NSString *docs = [[[NSFileManager defaultManager] URLsForDirectory:NSDocumentDirectory
                                                              inDomains:NSUserDomainMask].firstObject path];
    return @{
        @"version": @1,
        @"device": @{
            @"name": UIDevice.currentDevice.name ?: @"iPhone",
            @"id": KSRandomHex(8),
            @"secret": KSRandomHex(32)
        },
        @"share": @{
            @"root": [docs stringByAppendingPathComponent:@"KeShare"],
            @"receive_dir": @"Received",
            @"allow_upload": @YES,
            @"allow_clipboard": @YES,
            // 传输参数：1 MB 分块、4 路并发、1 MB socket 缓冲、不限速
            @"max_speed_kbps": @0,
            @"max_file_mb": @8192,
            @"chunk_kb": @1024,
            @"concurrency": @4,
            @"socket_buffer_kb": @1024
        },
        @"network": @{
            @"port": @(KS_DATA_PORT),
            @"auto_accept_pairing": @NO,
            @"discovery": @"both",
            @"bind_address": @"0.0.0.0"
        },
        @"ui": @{ @"theme": @"dark", @"language": @"zh-CN", @"start_minimized": @NO },
        @"developer": @{ @"unlocked": @NO, @"verbose_logging": @NO, @"log_ring_size": @2000 }
    };
}

static void mergeDefaults(NSMutableDictionary *dst, NSDictionary *def)
{
    for (NSString *k in def) {
        id dv = def[k];
        id cur = dst[k];
        if (!cur) { dst[k] = dv; continue; }
        if ([dv isKindOfClass:[NSDictionary class]] && [cur isKindOfClass:[NSDictionary class]]) {
            NSMutableDictionary *sub = [cur mutableCopy];
            mergeDefaults(sub, dv);
            dst[k] = sub;
        }
    }
}

- (void)reload
{
    NSData *d = [NSData dataWithContentsOfURL:self.configFileURL];
    NSMutableDictionary *dict = nil;
    if (d) {
        id obj = [NSJSONSerialization JSONObjectWithData:d options:0 error:NULL];
        if ([obj isKindOfClass:[NSDictionary class]]) dict = [obj mutableCopy];
    }
    if (!dict) dict = [NSMutableDictionary dictionary];
    mergeDefaults(dict, [self defaults]);
    self.root = dict;

    // 首次运行时创建共享目录
    [[NSFileManager defaultManager] createDirectoryAtPath:self.shareRoot
                              withIntermediateDirectories:YES attributes:nil error:NULL];
    [[NSFileManager defaultManager] createDirectoryAtPath:[self receivePath]
                              withIntermediateDirectories:YES attributes:nil error:NULL];
}

- (BOOL)save
{
    NSError *err = nil;
    NSData *d = [NSJSONSerialization dataWithJSONObject:self.root
                                               options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys
                                                 error:&err];
    if (!d) { [[KSLog shared] error:@"保存配置失败: %@", err.localizedDescription]; return NO; }
    BOOL ok = [d writeToURL:self.configFileURL atomically:YES];
    if (ok) [[NSNotificationCenter defaultCenter] postNotificationName:KSConfigDidChangeNotification object:self];
    return ok;
}

- (NSDictionary *)jsonRepresentation { return [self.root copy]; }

#define KS_GET(path, setter, getter, type) \
    - (type)getter { return [self valueForPath:path]; } \
    - (void)setter:(type)v { [self setValue:v forPath:path]; }

- (id)valueForPath:(NSString *)path
{
    NSArray *parts = [path componentsSeparatedByString:@"."];
    id cur = self.root;
    for (NSString *p in parts) {
        if (![cur isKindOfClass:[NSDictionary class]]) return nil;
        cur = cur[p];
    }
    return cur;
}

- (void)setValue:(id)v forPath:(NSString *)path
{
    NSArray *parts = [path componentsSeparatedByString:@"."];
    NSMutableDictionary *cur = self.root;
    for (NSUInteger i = 0; i + 1 < parts.count; i++) {
        NSMutableDictionary *next = [cur[parts[i]] mutableCopy];
        if (!next) next = [NSMutableDictionary dictionary];
        cur[parts[i]] = next;
        cur = next;
    }
    cur[parts.lastObject] = v ?: [NSNull null];
}

- (NSString *)deviceName { return [self valueForPath:@"device.name"]; }
- (void)setDeviceName:(NSString *)v { [self setValue:v forPath:@"device.name"]; }
- (NSString *)deviceId { return [self valueForPath:@"device.id"]; }
- (void)setDeviceId:(NSString *)v { [self setValue:v forPath:@"device.id"]; }
- (NSString *)secret { return [self valueForPath:@"device.secret"]; }
- (void)setSecret:(NSString *)v { [self setValue:v forPath:@"device.secret"]; }
- (NSString *)shareRoot { return [self valueForPath:@"share.root"]; }
- (void)setShareRoot:(NSString *)v { [self setValue:v forPath:@"share.root"]; }
- (NSString *)receiveDirName { return [self valueForPath:@"share.receive_dir"]; }
- (void)setReceiveDirName:(NSString *)v { [self setValue:v forPath:@"share.receive_dir"]; }
- (BOOL)allowUpload { return [[self valueForPath:@"share.allow_upload"] boolValue]; }
- (void)setAllowUpload:(BOOL)v { [self setValue:@(v) forPath:@"share.allow_upload"]; }
- (BOOL)allowClipboard { return [[self valueForPath:@"share.allow_clipboard"] boolValue]; }
- (void)setAllowClipboard:(BOOL)v { [self setValue:@(v) forPath:@"share.allow_clipboard"]; }
- (NSInteger)maxSpeedKBps { return [[self valueForPath:@"share.max_speed_kbps"] integerValue]; }
- (void)setMaxSpeedKBps:(NSInteger)v { [self setValue:@(v) forPath:@"share.max_speed_kbps"]; }
- (NSInteger)maxFileMB { return [[self valueForPath:@"share.max_file_mb"] integerValue]; }
- (void)setMaxFileMB:(NSInteger)v { [self setValue:@(v) forPath:@"share.max_file_mb"]; }
- (NSInteger)concurrency { return [[self valueForPath:@"share.concurrency"] integerValue] ?: 1; }
- (void)setConcurrency:(NSInteger)v { [self setValue:@(MAX(1, v)) forPath:@"share.concurrency"]; }

- (NSInteger)chunkKB { return [[self valueForPath:@"share.chunk_kb"] integerValue]; }
- (void)setChunkKB:(NSInteger)v { [self setValue:@(v) forPath:@"share.chunk_kb"]; }
- (NSInteger)port { return [[self valueForPath:@"network.port"] integerValue]; }
- (void)setPort:(NSInteger)v { [self setValue:@(v) forPath:@"network.port"]; }
- (BOOL)autoAcceptPairing { return [[self valueForPath:@"network.auto_accept_pairing"] boolValue]; }
- (void)setAutoAcceptPairing:(BOOL)v { [self setValue:@(v) forPath:@"network.auto_accept_pairing"]; }
- (NSString *)discoveryMode { return [self valueForPath:@"network.discovery"]; }
- (void)setDiscoveryMode:(NSString *)v { [self setValue:v forPath:@"network.discovery"]; }
- (NSString *)theme { return [self valueForPath:@"ui.theme"]; }
- (void)setTheme:(NSString *)v { [self setValue:v forPath:@"ui.theme"]; }
- (BOOL)developerLocked { return [[self valueForPath:@"developer.locked"] boolValue]; }
- (void)setDeveloperLocked:(BOOL)v { [self setValue:@(v) forPath:@"developer.locked"]; }

- (NSInteger)devGateMode { return [[self valueForPath:@"developer.gate_mode"] integerValue]; }
- (void)setDevGateMode:(NSInteger)mode { [self setValue:@(mode) forPath:@"developer.gate_mode"]; }

- (BOOL)autoConnect { return [[self valueForPath:@"network.auto_connect"] boolValue]; }
- (void)setAutoConnect:(BOOL)v { [self setValue:@(v) forPath:@"network.auto_connect"]; }
- (BOOL)developerUnlocked { return [[self valueForPath:@"developer.unlocked"] boolValue]; }
- (void)setDeveloperUnlocked:(BOOL)v { [self setValue:@(v) forPath:@"developer.unlocked"]; }
- (BOOL)verboseLogging { return [[self valueForPath:@"developer.verbose_logging"] boolValue]; }
- (void)setVerboseLogging:(BOOL)v { [self setValue:@(v) forPath:@"developer.verbose_logging"]; }
- (NSInteger)logRingSize { return [[self valueForPath:@"developer.log_ring_size"] integerValue]; }
- (void)setLogRingSize:(NSInteger)v { [self setValue:@(v) forPath:@"developer.log_ring_size"]; }

- (NSArray<NSDictionary *> *)knownDevices
{
    NSArray *list = [self valueForPath:@"history"];
    return [list isKindOfClass:[NSArray class]] ? list : @[];
}

- (void)rememberDevice:(KSDevice *)device
{
    if (device.deviceId.length == 0) return;
    NSMutableArray *list = [[self knownDevices] mutableCopy];
    [list filterUsingPredicate:[NSPredicate predicateWithFormat:@"did != %@", device.deviceId]];
    [list insertObject:@{
        @"did": device.deviceId,
        @"name": device.name ?: @"",
        @"kind": device.kindLabel ?: @"",
        @"host": device.host ?: @"",
        @"port": @(device.port),
        @"seen": @((long)[NSDate date].timeIntervalSince1970)
    } atIndex:0];
    while (list.count > 20) [list removeLastObject];
    [self setValue:list forPath:@"history"];
    [self save];
}

- (void)forgetDeviceWithId:(NSString *)deviceId
{
    NSMutableArray *list = [[self knownDevices] mutableCopy];
    [list filterUsingPredicate:[NSPredicate predicateWithFormat:@"did != %@", deviceId ?: @""]];
    [self setValue:list forPath:@"history"];
    [self save];
}

- (NSString *)hotspotSSID
{
    NSString *did = self.deviceId ?: @"0000";
    NSString *tail = did.length >= 4 ? [did substringFromIndex:did.length - 4] : did;
    return [NSString stringWithFormat:@"KeShare-%@", tail.uppercaseString];
}

- (NSString *)hotspotPassword
{
    return @"keshare123";
}

- (NSString *)receivePath
{
    // 对外可见的只有「接收的数据」这一个目录
    NSURL *docs = [[[NSFileManager defaultManager] URLsForDirectory:NSDocumentDirectory
                                                          inDomains:NSUserDomainMask] firstObject];
    NSString *dir = [[docs URLByAppendingPathComponent:@"接收的数据" isDirectory:YES] path];
    [[NSFileManager defaultManager] createDirectoryAtPath:dir
                              withIntermediateDirectories:YES attributes:nil error:NULL];
    return dir;
}

- (uint32_t)caps
{
    uint32_t c = KS_CAP_FILES | KS_CAP_BT | KS_CAP_MULTI;
    if (self.allowClipboard) c |= KS_CAP_PASTE;
    if (self.allowUpload) c |= KS_CAP_ACCEPT_FT | KS_CAP_RECV_ON;
    return c;
}

@end

#pragma mark - 日志

@interface KSLog ()
@property (nonatomic, strong) NSMutableArray<NSString *> *buffer;
@property (nonatomic, strong) NSDateFormatter *fmt;
@property (nonatomic, strong) dispatch_queue_t q;
@end

@implementation KSLog

+ (KSLog *)shared
{
    static KSLog *l;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ l = [[KSLog alloc] init]; });
    return l;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _buffer = [NSMutableArray array];
        _q = dispatch_queue_create("keshare.log", DISPATCH_QUEUE_SERIAL);
        _fmt = [[NSDateFormatter alloc] init];
        _fmt.dateFormat = @"HH:mm:ss.SSS";
    }
    return self;
}

- (void)append:(NSString *)level format:(NSString *)format args:(va_list)args
{
    NSString *msg = [[NSString alloc] initWithFormat:format arguments:args];
    NSString *line = [NSString stringWithFormat:@"[%@] %-5s %@", [self.fmt stringFromDate:[NSDate date]],
                      level.UTF8String, msg];
    dispatch_async(self.q, ^{
        [self.buffer addObject:line];
        NSInteger cap = MAX(200, KSConfig.shared.logRingSize);
        while ((NSInteger)self.buffer.count > cap) [self.buffer removeObjectAtIndex:0];
        dispatch_async(dispatch_get_main_queue(), ^{
            [[NSNotificationCenter defaultCenter] postNotificationName:KSLogDidChangeNotification object:nil];
        });
    });
    if (KSConfig.shared.verboseLogging) NSLog(@"[KeShare] %@", line);
}

- (void)log:(NSString *)format, ... { va_list a; va_start(a, format); [self append:@"INFO" format:format args:a]; va_end(a); }
- (void)info:(NSString *)format, ... { va_list a; va_start(a, format); [self append:@"INFO" format:format args:a]; va_end(a); }
- (void)warn:(NSString *)format, ... { va_list a; va_start(a, format); [self append:@"WARN" format:format args:a]; va_end(a); }
- (void)error:(NSString *)format, ... { va_list a; va_start(a, format); [self append:@"ERROR" format:format args:a]; va_end(a); }

- (NSArray<NSString *> *)lines
{
    __block NSArray *out;
    dispatch_sync(self.q, ^{ out = [self.buffer copy]; });
    return out;
}

- (NSString *)fullText
{
    NSMutableString *s = [NSMutableString string];
    [s appendFormat:@"KeShare %@ (%@) 日志\n", KS_VERSION_STRING, KS_BUILD_STRING];
    [s appendFormat:@"导出时间: %@\n设备: %@ (%@)\n\n", [NSDate date], KSConfig.shared.deviceName,
        KSConfig.shared.deviceId];
    for (NSString *l in [self lines]) [s appendFormat:@"%@\n", l];
    return s;
}

- (BOOL)exportToURL:(NSURL *)url error:(NSError **)error
{
    return [[self fullText] writeToURL:url atomically:YES encoding:NSUTF8StringEncoding error:error];
}

- (void)clear
{
    dispatch_async(self.q, ^{ [self.buffer removeAllObjects]; });
}

@end

