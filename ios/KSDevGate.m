//
//  KSDevGate.m -- 开发者模式门禁
//  KeShare 0.0.1 beta1
//
//  密码以密文形式写死，运行时用内置密钥解出后比对；
//  明文不出现在二进制字符串表里（可用 `strings` 验证）。
//
//  算法：keystream = SHA-256(key)，cipher = plain XOR keystream（循环）。
//  属于轻量混淆，不是强加密，目的是防止密码被直接扫出来。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <CommonCrypto/CommonDigest.h>
#import <CommonCrypto/CommonHMAC.h>

@implementation KSDevGate

static const uint8_t kKeyBytes[] = { 'K','e','S','h','a','r','e','5','7','6' };

static const uint8_t kCipherBytes[] = {
    0x11, 0x3e, 0xea, 0xc0, 0x6b, 0xc8, 0x49, 0xb7, 0x0b, 0x30, 0xd0, 0x1f, 0xf6, 0xe8,
    0x2f, 0x59, 0x77, 0x43, 0xe2, 0x0b, 0xb1, 0xed, 0x87, 0x0e, 0x1d, 0xd5, 0x40
};

+ (NSString *)decryptedPassword
{
    uint8_t stream[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(kKeyBytes, (CC_LONG)sizeof(kKeyBytes), stream);

    size_t length = sizeof(kCipherBytes);
    uint8_t plain[sizeof(kCipherBytes) + 1];
    for (size_t i = 0; i < length; i++) {
        plain[i] = kCipherBytes[i] ^ stream[i % CC_SHA256_DIGEST_LENGTH];
    }
    plain[length] = 0;
    return [NSString stringWithUTF8String:(const char *)plain];
}

+ (BOOL)verifyPassword:(NSString *)candidate
{
    if (candidate.length == 0) return NO;
    NSString *expected = [self decryptedPassword];
    NSData *a = [candidate dataUsingEncoding:NSUTF8StringEncoding];
    NSData *b = [expected dataUsingEncoding:NSUTF8StringEncoding];
    if (a.length != b.length) return NO;
    // 常量时间比较
    const uint8_t *pa = a.bytes;
    const uint8_t *pb = b.bytes;
    uint8_t diff = 0;
    for (NSUInteger i = 0; i < a.length; i++) {
        diff |= (uint8_t)(pa[i] ^ pb[i]);
    }
    return diff == 0;
}

@end
