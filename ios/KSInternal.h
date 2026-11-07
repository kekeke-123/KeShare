//
//  KSInternal.h -- 内部共享声明（不对外）
//
//  这些符号被多个 .m 使用，统一在这里声明，定义放在 KSInternal.m /
//  KSNetData.m 里，避免每个文件各写一份 static 造成重复定义或链接失败。
//

#import "KeShare.h"

NS_ASSUME_NONNULL_BEGIN

#pragma mark - 主题（KSApp.m 里实现）

@interface KSTheme : NSObject
@property (class, nonatomic, readonly) KSTheme *shared;
@property (nonatomic, strong) UIColor *accent;
@property (nonatomic, strong) UIColor *accent2;
@property (nonatomic, strong) UIColor *textPrimary;
@property (nonatomic, strong) UIColor *textSecondary;
@property (nonatomic, strong) UIColor *cardStroke;
@end

UIColor *KSThemeTextSecondary(void);
UIColor *KSSystemAccent(void);

/// BLE 链路额外的控制方法（KSBleLink 实现，避免用 performSelector 触发 ARC 警告）
@protocol KSDirectLink <KSControlLink>
- (void)startAdvertising;
- (void)startScanning;
- (void)stopScanning;
- (BOOL)connectPeripheral:(id)peripheral;
@end

/// 蓝牙发现：扫描到附近设备时回调
@protocol KSDiscoveryLink <KSDirectLink>
@property (nonatomic, copy, nullable) void (^onPeerFound)(KSDevice *peer, id peripheral);
@end

/// KSServer 的蓝牙发现入口（实现在 KSNetData.m）
@interface KSServer (Discovery)
- (nullable id<KSDiscoveryLink>)bleLink;
- (void)autoConnectKnownPeers;
@end
#pragma mark - UDP 发现（KSDisc.m）

void KSDiscoveryStart(void);
void KSDiscoveryStop(void);

#pragma mark - 网络小工具（KSInternal.m 里实现）

BOOL KSReadFull(int fd, void *buf, size_t len);
void KSWriteFull(int fd, const void *buf, size_t len);
void KSSetNoDelay(int fd);
NSString *KSIPv4StringLocal(void);

#pragma mark - 待传输登记（定义在 KSNetData.m）

@interface KSPendingTransfer : NSObject
@property (nonatomic, copy) NSString *transferId;
@property (nonatomic, copy, nullable) NSString *tempPath;
@property (nonatomic, copy, nullable) NSString *finalPath;
@property (nonatomic, copy, nullable) NSString *expectedHash;
@property (nonatomic, copy, nullable) NSString *servePath;     // 非空 = 本机是发送方，要写数据帧
@property (nonatomic, assign) BOOL awaitingAck;                // 已发完，等对端 hash_resp
@property (nonatomic, assign) unsigned long long totalSize;
@property (nonatomic, assign) unsigned long long done;
@property (nonatomic, strong, nullable) KSTransfer *publicTransfer;
@end

@interface KSDataRegistry : NSObject
@property (class, nonatomic, readonly) KSDataRegistry *shared;
- (void)addPending:(KSPendingTransfer *)p;
- (nullable KSPendingTransfer *)pendingForId:(NSString *)tid;
- (void)removePendingForId:(NSString *)tid;
@end

/// KSServer 的发送/请求处理（实现在 KSNetServe.m）
@interface KSServer (Serve)
@property (nonatomic, strong, nullable) id<KSControlLink> controlLink;
- (void)addPeer:(KSDevice *)peer;
- (void)serveTransfer:(KSPendingTransfer *)pt onSocket:(int)fd;
- (void)handleGetRequest:(NSDictionary *)msg link:(id<KSControlLink>)link;
- (void)handlePutOffer:(NSDictionary *)msg link:(id<KSControlLink>)link;
- (void)handleHashAck:(NSDictionary *)msg;
@end
#pragma mark - 数据接收端（定义在 KSSession.m）

@interface KSDataReceiver : NSObject
+ (void)receiveFromHost:(NSString *)host
                   port:(uint16_t)port
                  token:(NSString *)token
             transferId:(NSString *)tid
                   size:(unsigned long long)size
            destination:(NSString *)dest
             onProgress:(nullable void (^)(unsigned long long done))progress
             completion:(void (^)(NSString *_Nullable tempPath, NSString *_Nullable error))completion;
@end

NS_ASSUME_NONNULL_END
