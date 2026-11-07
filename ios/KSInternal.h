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
@interface KSServer (Sessions)
@property (nonatomic, strong) NSMutableDictionary<NSString *, KSSession *> *sessions;
@end

@interface KSServer (Discovery)
- (nullable id<KSDiscoveryLink>)bleLink;
- (void)autoConnectKnownPeers;
@end
/// SHA-256（CommonCrypto）：完整性摘要与 token 都用它
NSString *KSSHA256HexOfData(NSData *data);
NSString *KSSHA256HexOfFile(NSString *path, NSError **error);
#pragma mark - Haze 视觉语言（KSHaze.m）

extern CGFloat const KSHazeRadiusPanel;
extern CGFloat const KSHazeRadiusPill;
extern CGFloat const KSHazeBorderAlpha;
extern CGFloat const KSHazeHighlightAlpha;

/// 磨砂玻璃面板：系统材质 + 底色叠加 + 顶部柔光 + 1px 描边
@interface KSHaze : UIView
@property (nonatomic, assign) CGFloat radius;
@property (nonatomic, assign) CGFloat alphaWeight;
@property (nonatomic, strong, readonly) UIView *content;
@property (nonatomic, strong) UIVisualEffectView *blurView;
@property (nonatomic, strong) UIView *tintView;
@property (nonatomic, strong) UIView *highlightView;
@property (nonatomic, strong) CAGradientLayer *highlightLayer;
+ (UIColor *)tintColor;
+ (UIColor *)panelColor;
+ (UIBlurEffect *)blurEffect;
+ (instancetype)panelWithFrame:(CGRect)frame radius:(CGFloat)radius;
+ (instancetype)pillWithFrame:(CGRect)frame;
@end
#pragma mark - 自适应根容器（KSRoot.m）

/// 宽屏（>=700pt）返回侧边栏容器，窄屏返回 nil（由调用方回退到标签栏）
UIViewController *KSRootMakeIfWide(NSArray<UINavigationController *> *pages,
                                   NSArray<NSString *> *titles,
                                   NSArray<NSString *> *glyphs);
#pragma mark - 法律性文稿（KSLegal.m）

UIViewController *KSLegalMakeViewController(void (^onFinish)(BOOL accepted));
/// 未同意时以全屏方式弹出；同意后回调 onAccepted
void KSLegalPresentIfNeeded(UIViewController *host, void (^onAccepted)(void));
#pragma mark - 断点续传（KSResume.m）

@interface KSResumeStore : NSObject
+ (NSString *)directory;
+ (NSString *)keyForPath:(NSString *)path size:(long long)size hash:(NSString *)hash;
+ (NSString *)partPathForKey:(NSString *)key;
+ (NSString *)metaPathForKey:(NSString *)key;
/// 返回 {path,size,hash,offset,peer,direction,mtime}；无有效断点返回 nil
+ (NSDictionary *)entryForKey:(NSString *)key;
+ (BOOL)saveEntry:(NSDictionary *)entry key:(NSString *)key error:(NSError **)error;
+ (void)removeEntryForKey:(NSString *)key;
/// days <= 0 表示永不清理
+ (NSInteger)cleanupExpiredWithKeepDays:(NSInteger)days;
+ (unsigned long long)totalBytes;
@end
#pragma mark - UDP 发现（KSDisc.m）

void KSDiscoveryStart(void);
void KSDiscoveryStop(void);

#pragma mark - 局域网 HTTP / WebSocket 通道（KSWeb.m）

/// 首行是否像 HTTP 请求（GET / PUT / HEAD ...）
BOOL KSWebLooksLikeHTTP(NSString *firstLine);
/// 处理一条 HTTP 或 WebSocket 连接；firstLine 是已经读掉的首行
void KSWebServe(int fd, NSString *firstLine, NSString *peerIp, void (^emit)(NSDictionary *info));

#pragma mark - 网络小工具（KSInternal.m 里实现）

BOOL KSReadFull(int fd, void *buf, size_t len);
void KSWriteFull(int fd, const void *buf, size_t len);
void KSSetNoDelay(int fd);
NSString *KSIPv4StringLocal(void);
/// 在 dir 下为 name 找一个不冲突的路径（"报告.pdf" → "报告 2.pdf"），避免覆盖已接收文件
NSString *KSUniquePathInDirectory(NSString *dir, NSString *name);

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
/// 触发本次传输的控制链路：发完数据后要在这条链路上声明摘要（不能写到别的连接上）
@property (nonatomic, strong, nullable) id<KSControlLink> requestLink;
@end

#pragma mark - 服务端控制链路（accepted socket 上的一条 CTL 会话）

/// KSServer 侧每条 accepted 控制连接对应一个这样的对象：回包必须写回同一 socket
@protocol KSServerControlLink <KSControlLink>
@property (nonatomic, copy) NSString *nonce;             // 本端在这次握手里的 nonce
@property (nonatomic, copy, nullable) NSString *peerDid;
@property (nonatomic, copy, nullable) NSString *remoteIp;
@end

/// token = hex(SHA-256("KS1-CTL\0" + did_A + nonce_A + did_B + nonce_B))[:16]
/// A 永远是握手的发起方。
NSString *KSTokenFor(NSString *didA, NSString *nonceA, NSString *didB, NSString *nonceB);

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
- (void)handleListRequest:(NSDictionary *)msg link:(id<KSControlLink>)link;
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
               transfer:(nullable KSTransfer *)transfer
             onProgress:(nullable void (^)(unsigned long long done))progress
             completion:(void (^)(NSString *_Nullable tempPath, NSString *_Nullable error))completion;
@end

NS_ASSUME_NONNULL_END
