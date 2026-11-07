//
//  KSWiFi.m -- 热点自动加入 + 当前 Wi-Fi 信息
//  KeShare 1.0.5
//
//  依赖证书能力（你的证书已包含）：
//    * 热点配置        com.apple.developer.networking.HotspotConfiguration
//    * 访问 Wi-Fi 信息 com.apple.developer.networking.wifi-info
//
//  具备这两个能力时，App 内点一下即可加入电脑开的热点，无需跳系统设置。
//

#import "KeShare.h"
#import "KSInternal.h"
#import <NetworkExtension/NetworkExtension.h>
#import <SystemConfiguration/CaptiveNetwork.h>
#import <arpa/inet.h>
#import <ifaddrs.h>

@implementation KSWiFi

+ (BOOL)hotspotJoinAvailable
{
    if (@available(iOS 11.0, *)) {
        return [NEHotspotConfigurationManager class] != Nil;
    }
    return NO;
}

+ (void)currentSSID:(void (^)(NSString *ssid))completion
{
    NSString *ssid = @"";
    NSArray *interfaces = (__bridge_transfer NSArray *)CNCopySupportedInterfaces();
    for (NSString *name in interfaces) {
        NSDictionary *info = (__bridge_transfer NSDictionary *)CNCopyCurrentNetworkInfo(
            (__bridge CFStringRef)name);
        if ([info[@"SSID"] isKindOfClass:[NSString class]] && [info[@"SSID"] length]) {
            ssid = info[@"SSID"];
            break;
        }
    }
    if (completion) completion(ssid);
}

+ (void)joinHotspot:(NSString *)ssid
           password:(NSString *)password
         completion:(void (^)(BOOL ok, NSString *message))completion
{
    if (ssid.length == 0) {
        if (completion) completion(NO, @"热点名称为空");
        return;
    }
    if (@available(iOS 11.0, *)) {
        NEHotspotConfiguration *config =
            [[NEHotspotConfiguration alloc] initWithSSID:ssid
                                               passphrase:password ?: @""
                                                    isWEP:NO];
        config.joinOnce = NO;   // 保留配置，下次可直接连
        [[NEHotspotConfigurationManager sharedManager]
            applyConfiguration:config
            completionHandler:^(NSError *error) {
                dispatch_async(dispatch_get_main_queue(), ^{
                    if (!error) {
                        if (completion) completion(YES, @"已连接热点");
                        return;
                    }
                    // NEHotspotConfigurationErrorAlreadyAssociated = 5
                    if (error.code == 5 || error.code == 7) {
                        if (completion) completion(YES, @"已在该热点");
                        return;
                    }
                    NSString *msg = error.localizedDescription ?: @"加入热点失败";
                    if (error.code == 8) msg = @"密码错误";
                    if (error.code == 13) msg = @"用户取消";
                    if (completion) completion(NO, msg);
                });
            }];
        return;
    }
    if (completion) completion(NO, @"系统版本过低，请在设置中手动连接");
}

+ (NSString *)hotspotGatewayAddress
{
    // iPhone 个人热点的网关固定为 172.20.10.1；Windows 移动热点通常是 192.168.137.1
    struct ifaddrs *ifa = NULL;
    NSString *fallback = @"";
    if (getifaddrs(&ifa) != 0) return fallback;
    for (struct ifaddrs *p = ifa; p; p = p->ifa_next) {
        if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET) continue;
        char buf[INET_ADDRSTRLEN];
        struct sockaddr_in *sin = (struct sockaddr_in *)p->ifa_addr;
        if (!inet_ntop(AF_INET, &sin->sin_addr, buf, sizeof buf)) continue;
        NSString *ip = [NSString stringWithUTF8String:buf];
        if ([ip hasPrefix:@"172.20.10."]) { fallback = @"172.20.10.1"; break; }
        if ([ip hasPrefix:@"192.168.137."]) { fallback = @"192.168.137.1"; break; }
    }
    freeifaddrs(ifa);
    return fallback;
}

@end
