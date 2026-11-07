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
@end
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
