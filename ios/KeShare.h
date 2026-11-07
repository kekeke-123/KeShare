//
//  KeShare.h -- 公共头文件
//  KeShare 1.0 for iOS (arm64)
//

#import <UIKit/UIKit.h>
#import <Foundation/Foundation.h>
#define KS_HASH_BYTES   32
#define KS_HASH_HEX_LEN 65

NS_ASSUME_NONNULL_BEGIN

#define KS_VERSION_STRING   @"0.0.1 beta1"
#define KS_BUILD_STRING     @"1"
#define KS_PROTO_VERSION    1
#define KS_DATA_PORT        47377

#define KS_SERVICE_UUID     @"5908C1A1-7F4E-4B2A-9C37-0B7C1B3B0001"
#define KS_CHAR_RX_UUID     @"5908C1A1-7F4E-4B2A-9C37-0B7C1B3B0002"
#define KS_CHAR_TX_UUID     @"5908C1A1-7F4E-4B2A-9C37-0B7C1B3B0003"

#define KS_CAP_FILES        (1u << 0)
#define KS_CAP_PASTE        (1u << 1)
#define KS_CAP_ACCEPT_FT    (1u << 2)
#define KS_CAP_RECV_ON      (1u << 3)
#define KS_CAP_BT           (1u << 4)
#define KS_CAP_MULTI        (1u << 5)
#define KS_CAP_RESUME       (1u << 6)
#define KS_CAPS_DEFAULT     (KS_CAP_FILES | KS_CAP_PASTE | KS_CAP_ACCEPT_FT | KS_CAP_RECV_ON | KS_CAP_BT | KS_CAP_MULTI)

extern NSNotificationName const KSConfigDidChangeNotification;
extern NSNotificationName const KSLogDidChangeNotification;
extern NSNotificationName const KSPeersDidChangeNotification;
extern NSNotificationName const KSTransfersDidChangeNotification;

#pragma mark - 设备

typedef NS_ENUM(NSInteger, KSDeviceKind) {
    KSDeviceKindUnknown = 0,
    KSDeviceKindIOS,
    KSDeviceKindWindows,
    KSDeviceKindMac,
    KSDeviceKindAndroid
};

@interface KSDevice : NSObject
@property (nonatomic, copy) NSString *deviceId;
@property (nonatomic, copy) NSString *name;
@property (nonatomic, copy) NSString *osVersion;
@property (nonatomic, assign) KSDeviceKind kind;
@property (nonatomic, copy) NSString *host;          // IPv4 / Bonjour 主机名
@property (nonatomic, assign) uint16_t port;
@property (nonatomic, assign) uint32_t caps;
@property (nonatomic, assign) BOOL connected;
@property (nonatomic, assign) BOOL trusted;
@property (nonatomic, strong, nullable) id link;      // 控制链路（KSControlLink）
+ (instancetype)deviceWithDictionary:(NSDictionary *)dict;
- (NSString *)kindLabel;
@end

#pragma mark - 传输任务

typedef NS_ENUM(NSInteger, KSTransferDirection) {
    KSTransferDirectionDownload = 0,
    KSTransferDirectionUpload
};

typedef NS_ENUM(NSInteger, KSTransferState) {
    KSTransferStateWaiting = 0,
    KSTransferStateRunning,
    KSTransferStateVerifying,
    KSTransferStateDone,
    KSTransferStateFailed
};

@interface KSTransfer : NSObject
@property (nonatomic, copy) NSString *transferId;     // 8 hex
@property (nonatomic, copy) NSString *fileName;
@property (nonatomic, copy) NSString *peerName;
@property (nonatomic, copy) NSString *path;
@property (nonatomic, assign) KSTransferDirection direction;
@property (nonatomic, assign) KSTransferState state;
@property (nonatomic, assign) unsigned long long done;
@property (nonatomic, assign) unsigned long long total;
@property (nonatomic, copy, nullable) NSString *errorText;
- (double)progress;
- (NSString *)stateLabel;
@end

#pragma mark - 配置（settings.json）

@interface KSConfig : NSObject

@property (class, nonatomic, readonly) KSConfig *shared;

@property (nonatomic, copy) NSString *deviceName;
@property (nonatomic, copy) NSString *deviceId;
@property (nonatomic, copy) NSString *secret;
@property (nonatomic, copy) NSString *shareRoot;        // 分享目录（绝对路径）
@property (nonatomic, copy) NSString *receiveDirName;   // 接收子目录名
@property (nonatomic, assign) BOOL allowUpload;
@property (nonatomic, assign) BOOL allowClipboard;
@property (nonatomic, assign) NSInteger maxSpeedKBps;   // 0 = 不限速
@property (nonatomic, assign) NSInteger maxFileMB;
@property (nonatomic, assign) NSInteger chunkKB;
@property (nonatomic, assign) NSInteger port;
@property (nonatomic, assign) BOOL autoAcceptPairing;
@property (nonatomic, copy) NSString *discoveryMode;    // both / ble / wifi / off
@property (nonatomic, copy) NSString *theme;            // dark / light / auto
@property (nonatomic, assign) BOOL autoConnect;              // 自动连接历史设备
@property (nonatomic, assign) BOOL developerUnlocked;
@property (nonatomic, assign) BOOL verboseLogging;
@property (nonatomic, assign) NSInteger logRingSize;

@property (nonatomic, readonly) NSString *receivePath;      // 分享根目录下的接收子目录（绝对路径）
@property (nonatomic, readonly) NSURL *configFileURL;
@property (nonatomic, readonly) NSString *configFilePath;
@property (nonatomic, readonly) uint32_t caps;

- (void)reload;
- (BOOL)save;
/// 历史设备（自动连接用）
- (NSArray<NSDictionary *> *)knownDevices;
- (void)rememberDevice:(KSDevice *)device;
- (void)forgetDeviceWithId:(NSString *)deviceId;
/// 热点信息（引导用）
- (NSString *)hotspotSSID;
- (NSString *)hotspotPassword;
- (NSDictionary *)jsonRepresentation;

@end

#pragma mark - 日志

@interface KSLog : NSObject
@property (class, nonatomic, readonly) KSLog *shared;
- (void)log:(NSString *)format, ... NS_FORMAT_FUNCTION(1, 2);
- (void)info:(NSString *)format, ... NS_FORMAT_FUNCTION(1, 2);
- (void)warn:(NSString *)format, ... NS_FORMAT_FUNCTION(1, 2);
- (void)error:(NSString *)format, ... NS_FORMAT_FUNCTION(1, 2);
- (NSArray<NSString *> *)lines;
- (NSString *)fullText;
- (BOOL)exportToURL:(NSURL *)url error:(NSError **)error;
- (void)clear;
@end

#pragma mark - 控制链路（蓝牙，WiFi 回退）

@protocol KSControlLinkDelegate;

@protocol KSControlLink <NSObject>
@property (nonatomic, weak, nullable) id<KSControlLinkDelegate> delegate;
@property (nonatomic, readonly) BOOL isOpen;
@property (nonatomic, readonly) NSString *transportName;   // 蓝牙 / WiFi 回退
- (void)sendMessage:(NSDictionary *)message;
- (void)close;
@end

@protocol KSControlLinkDelegate <NSObject>
- (void)controlLinkDidOpen:(id<KSControlLink>)link;
- (void)controlLink:(id<KSControlLink>)link didReceiveMessage:(NSDictionary *)message;
- (void)controlLink:(id<KSControlLink>)link didFailWithError:(NSString *)error;
- (void)controlLinkDidClose:(id<KSControlLink>)link;
@end

#pragma mark - 会话（一个对端 = 一条控制链路 + 数据通道）

@class KSSession;
@protocol KSSessionDelegate <NSObject>
- (void)session:(KSSession *)session didUpdatePeer:(KSDevice *)peer;
- (void)session:(KSSession *)session didReceiveTransferUpdate:(KSTransfer *)transfer;
- (void)session:(KSSession *)session didFail:(NSString *)error;
@end

@interface KSSession : NSObject <KSControlLinkDelegate>
@property (nonatomic, readonly) KSDevice *peer;
@property (nonatomic, readonly) id<KSControlLink> link;
@property (nonatomic, weak, nullable) id<KSSessionDelegate> delegate;
@property (nonatomic, readonly) NSArray<NSDictionary *> *lastListing;

- (instancetype)initWithPeer:(KSDevice *)peer;
- (void)startAsInitiator:(BOOL)initiator;
- (void)requestListing;
- (void)downloadPath:(NSString *)path;
- (void)uploadFileAtPath:(NSString *)localPath;
- (void)sendClipboardText:(NSString *)text;
- (void)close;
@end

#pragma mark - 服务器（接收端，数据通道）

@interface KSServer : NSObject
@property (class, nonatomic, readonly) KSServer *shared;
@property (nonatomic, readonly) NSArray<KSDevice *> *peers;
- (void)start;
- (void)restart;
- (void)stop;
- (void)refreshBonjour;
- (KSSession *)sessionForPeerId:(NSString *)deviceId;
- (NSArray<KSDevice *> *)knownPeers;
@end

#pragma mark - 工具

NSString *KSRandomHex(NSUInteger bytes);
/// 计算文件/数据的 SHA-KE-1024（256 位十六进制小写字符）
NSString *KSHashOfFile(NSString *path, NSError **error);
NSString *KSHashOfData(NSData *data);
unsigned long long KSFileSize(NSString *path);
NSString *KSFormatBytes(unsigned long long bytes);
BOOL KSIsValidRelativePath(NSString *path);
NSString *KSKindNameForDevice(KSDeviceKind kind);

#pragma mark - 界面基类

@interface KSViewController : UIViewController
@end

/// 液态玻璃卡片：iOS 26+ 用 UIGlassEffect，低版本退回 UIBlurEffect
@interface KSGlassView : UIView
@property (nonatomic, assign) CGFloat cornerRadius;
+ (instancetype)glassView;
@end

@interface KSBackgroundView : UIView
@end

@interface KSSectionHeaderView : UITableViewHeaderFooterView
@property (nonatomic, strong) UILabel *titleLabel;
@end

#pragma mark - 各页面

@interface KSDevicesViewController : KSViewController
@end

@interface KSFilesViewController : KSViewController
@end

@interface KSTransfersViewController : KSViewController
@end

@interface KSSettingsViewController : KSViewController
@end

@interface KSDeveloperViewController : KSViewController
@end

#pragma mark - 开发者模式门禁

@interface KSDevGate : NSObject
/// 解密后的开发者密码（仅用于内部校验，不要在 UI 上显示）
+ (NSString *)decryptedPassword;
/// 校验用户输入
+ (BOOL)verifyPassword:(NSString *)candidate;
@end

#pragma mark - 热点 / Wi-Fi

@interface KSWiFi : NSObject
/// 是否支持 App 内一键加入热点（需 HotspotConfiguration 能力）
+ (BOOL)hotspotJoinAvailable;
/// 加入指定热点（调用后系统会弹一次确认）
+ (void)joinHotspot:(NSString *)ssid
           password:(NSString *)password
         completion:(void (^)(BOOL ok, NSString *message))completion;
/// 当前 Wi-Fi 名称（需 Access WiFi Information 能力）
+ (void)currentSSID:(void (^)(NSString *ssid))completion;
/// 热点网关地址（172.20.10.1 / 192.168.137.1），空串表示没连热点
+ (NSString *)hotspotGatewayAddress;
@end

#pragma mark - 灵动岛 / 实时活动
//
// 真正的 ActivityKit 实现在 KSActivityManager.swift（Swift）；
// Objective-C 侧通过这些 C 函数调用，见 KSActivityShim.m。

void KSActivityStart(NSString *title, NSString *subtitle, unsigned long long total);
void KSActivityUpdate(double progress, NSString *detail);
void KSActivityFinish(BOOL success, NSString *message);
BOOL KSActivitySupported(void);

NS_ASSUME_NONNULL_END
