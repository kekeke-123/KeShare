//
//  KSGuard.m -- 敏感内容限制体系（状态机 + 持久化 + 隐藏模式）
//  KeShare 0.0.1 beta1
//
//  设计要点
//  --------
//  * 状态持久化在 **Keychain**：iOS 删除 App 会清空沙盒，只有 Keychain 能跨重装保留，
//    这是"重装也无法解除限制"在 iOS 上唯一可靠的做法（不存在"另一个目录"）。
//  * 存储内容为 AES 无关的简单混淆 JSON（Keychain 本身已由系统加密）。
//  * 设备序列号无法获取（iOS 7 起封锁），改用首次生成的 UUID，同样存 Keychain，
//    卸载重装不变，作为解除码的派生输入。
//  * 限制是**全设备**生效：禁止发送含恶意内容的文件 + 强制开启敏感内容提示。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <Security/Security.h>
#import <CommonCrypto/CommonHMAC.h>

static NSString *const kKSGuardService = @"com.keshare.guard";
static NSString *const kKSGuardAccount = @"state.v1";
static NSString *const kKSGuardUuidKey = @"device.uuid";

#pragma mark - Keychain 读写

static NSData *KSKeychainLoad(NSString *account)
{
    NSDictionary *query = @{
        (__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService: kKSGuardService,
        (__bridge id)kSecAttrAccount: account,
        (__bridge id)kSecReturnData: @YES,
        (__bridge id)kSecMatchLimit: (__bridge id)kSecMatchLimitOne
    };
    CFTypeRef out = NULL;
    OSStatus status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &out);
    if (status != errSecSuccess || !out) return nil;
    return (__bridge_transfer NSData *)out;
}

static BOOL KSKeychainStore(NSString *account, NSData *data)
{
    NSDictionary *base = @{
        (__bridge id)kSecClass: (__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService: kKSGuardService,
        (__bridge id)kSecAttrAccount: account,
        (__bridge id)kSecAttrAccessible: (__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    };
    SecItemDelete((__bridge CFDictionaryRef)base);
    NSMutableDictionary *item = [base mutableCopy];
    item[(__bridge id)kSecValueData] = data;
    OSStatus status = SecItemAdd((__bridge CFDictionaryRef)item, NULL);
    return status == errSecSuccess;
}

#pragma mark - 状态

@implementation KSGuard

+ (instancetype)shared
{
    static KSGuard *g;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ g = [[KSGuard alloc] init]; });
    return g;
}

- (instancetype)init
{
    if ((self = [super init])) {
        _state = KSGuardStateClear;
        _violations = @[];
        [self reload];
    }
    return self;
}

- (NSString *)deviceUUID
{
    NSData *raw = KSKeychainLoad(kKSGuardUuidKey);
    if (raw.length) return [[NSString alloc] initWithData:raw encoding:NSUTF8StringEncoding];
    NSString *uuid = [NSUUID UUID].UUIDString;
    KSKeychainStore(kKSGuardUuidKey, [uuid dataUsingEncoding:NSUTF8StringEncoding]);
    return uuid;
}

- (void)reload
{
    NSData *raw = KSKeychainLoad(kKSGuardAccount);
    if (!raw.length) {
        _state = KSGuardStateClear;
        _violations = @[];
        return;
    }
    NSDictionary *json = [NSJSONSerialization JSONObjectWithData:raw options:0 error:NULL];
    if (![json isKindOfClass:[NSDictionary class]]) return;

    _state = (KSGuardState)[json[@"state"] integerValue];
    _since = [json[@"since"] doubleValue];
    _cooldownUntil = [json[@"cooldown_until"] doubleValue];
    _hiddenCooldownUntil = [json[@"hidden_cooldown_until"] doubleValue];
    _violations = json[@"violations"] ?: @[];
    _reinstallPromptPending = [json[@"reinstall_pending"] boolValue];

    // 冷静期到期自动降级
    if (_state == KSGuardStateCooldown && _cooldownUntil > 0 &&
        [NSDate date].timeIntervalSince1970 > _cooldownUntil) {
        _state = KSGuardStateRestricted;
        _cooldownUntil = 0;
        [self persist];
    }
}

- (void)persist
{
    NSDictionary *json = @{
        @"state": @(_state),
        @"since": @(_since),
        @"cooldown_until": @(_cooldownUntil),
        @"hidden_cooldown_until": @(_hiddenCooldownUntil),
        @"violations": _violations ?: @[],
        @"reinstall_pending": @(_reinstallPromptPending)
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:json options:0 error:NULL];
    if (data) KSKeychainStore(kKSGuardAccount, data);
}

#pragma mark - 状态迁移

- (BOOL)restricted
{
    return _state == KSGuardStateRestricted ||
           _state == KSGuardStateCooldown ||
           _state == KSGuardStatePermanent;
}

- (BOOL)forcedSensitiveWarning
{
    // 受限后「敏感内容提示」强制开启且不可关闭
    return [self restricted];
}

- (void)recordViolationFromPeer:(NSString *)peer reason:(NSString *)reason
{
    NSMutableArray *list = [_violations mutableCopy] ?: [NSMutableArray array];
    [list insertObject:@{ @"ts": @([NSDate date].timeIntervalSince1970),
                          @"peer": peer ?: @"",
                          @"reason": reason ?: @"malware" } atIndex:0];
    while (list.count > 50) [list removeLastObject];
    _violations = list;

    double now = [NSDate date].timeIntervalSince1970;
    switch (_state) {
        case KSGuardStateClear:
            _state = KSGuardStateRestricted;
            _since = now;
            break;

        case KSGuardStateRestricted:
            // 受限期间再犯：进入 30 天冷静期
            _state = KSGuardStateCooldown;
            _cooldownUntil = now + 30 * 24 * 3600;
            break;

        case KSGuardStateCooldown: {
            // 冷静期内再犯 → 永久限制
            _state = KSGuardStatePermanent;
            break;
        }

        case KSGuardStatePermanent:
            if (_hiddenCooldownUntil > 0 && now < _hiddenCooldownUntil) {
                // 隐藏模式的 7 天冷静期内再犯 2 次 → 仍为永久
                _state = KSGuardStatePermanent;
            }
            break;
    }
    [self persist];
}

- (void)acknowledgeReinstall
{
    // "我改过自新了！" → 进入 30 天冷静期
    if (_state == KSGuardStateClear) return;
    _state = KSGuardStateCooldown;
    _cooldownUntil = [NSDate date].timeIntervalSince1970 + 30 * 24 * 3600;
    _reinstallPromptPending = NO;
    [self persist];
}

- (void)dismissReinstallPrompt
{
    // "滚！" → 保持原状态
    _reinstallPromptPending = NO;
    [self persist];
}

- (BOOL)markReinstallDetected
{
    // 由启动流程调用：若 Keychain 里已有受限状态，说明是重装后的首次启动
    if (![self restricted]) return NO;
    _reinstallPromptPending = YES;
    [self persist];
    return YES;
}

#pragma mark - 隐藏模式

- (BOOL)hiddenModeUnlockedWithDeviceName:(NSString *)name
{
    NSString *expect = [NSString stringWithFormat:@"%@sorry", KSConfig.shared.deviceId ?: @""];
    return [name.lowercaseString isEqualToString:expect.lowercaseString];
}

+ (BOOL)isSystemTestPhrase:(NSString *)text
{
    return [[text stringByTrimmingCharactersInSet:
             [NSCharacterSet whitespaceAndNewlineCharacterSet]] isEqualToString:@"systemtest"];
}

+ (BOOL)isRepentPhrase:(NSString *)text
{
    NSString *clean = [text stringByTrimmingCharactersInSet:
                       [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    return [clean isEqualToString:@"对不起！我错了！"];
}

- (void)applyHiddenUnlock:(NSString *)phrase
{
    if ([KSGuard isSystemTestPhrase:phrase]) {
        _state = KSGuardStateClear;
        _cooldownUntil = 0;
        _hiddenCooldownUntil = 0;
    } else if ([KSGuard isRepentPhrase:phrase]) {
        // 走"对不起！我错了！"仍保留 7 天冷静期，期内再犯 2 次 → 永久
        _state = KSGuardStateCooldown;
        _hiddenCooldownUntil = [NSDate date].timeIntervalSince1970 + 7 * 24 * 3600;
        _cooldownUntil = _hiddenCooldownUntil;
    }
    [self persist];
}

#pragma mark - 解除码

+ (NSString *)unlockCodeForDeviceUUID:(NSString *)uuid salt:(NSString *)salt
{
    // 8 位大写十六进制，基于 UUID + 内置盐做 HMAC-SHA256
    NSData *key = [@"KeShare-Unlock-2026" dataUsingEncoding:NSUTF8StringEncoding];
    NSData *msg = [[NSString stringWithFormat:@"%@|%@", uuid ?: @"", salt ?: @""]
                   dataUsingEncoding:NSUTF8StringEncoding];
    unsigned char out[CC_SHA256_DIGEST_LENGTH];
    CCHmac(kCCHmacAlgSHA256, key.bytes, key.length, msg.bytes, msg.length, out);
    char hex[9];
    for (int i = 0; i < 4; i++) sprintf(hex + i * 2, "%02X", out[i]);
    hex[8] = 0;
    return [NSString stringWithUTF8String:hex];
}

- (BOOL)applyUnlockCode:(NSString *)code
{
    NSString *expect = [KSGuard unlockCodeForDeviceUUID:[self deviceUUID] salt:@""];
    if (![[code.uppercaseString stringByTrimmingCharactersInSet:
           [NSCharacterSet whitespaceCharacterSet]] isEqualToString:expect]) {
        return NO;
    }
    _state = KSGuardStateClear;
    _cooldownUntil = 0;
    _hiddenCooldownUntil = 0;
    [self persist];
    return YES;
}

@end
