//
//  KSActivityShim.m -- 把 Objective-C 的调用转发到 Swift 的 KSActivityManager
//
//  之所以走 NSClassFromString：Swift 类由 swiftc 编入同一个二进制，
//  在 ObjC 侧没有生成的头文件可用（我们不依赖 Xcode 工程），
//  因此用运行时查找 + 显式声明方法签名。
//

#import "KeShare.h"
#import <objc/message.h>

#pragma mark - 供 ObjC 调用的 C 函数

static id KSActivityShared(void)
{
    Class cls = NSClassFromString(@"KSActivityManager");
    if (!cls) return nil;
    SEL sel = NSSelectorFromString(@"shared");
    if (![cls respondsToSelector:sel]) return nil;
    return ((id (*)(id, SEL))objc_msgSend)(cls, sel);
}

void KSActivityStart(NSString *title, NSString *subtitle, unsigned long long total)
{
    id mgr = KSActivityShared();
    if (!mgr) return;
    SEL sel = NSSelectorFromString(@"startWithTitle:subtitle:total:");
    if (![mgr respondsToSelector:sel]) return;
    ((void (*)(id, SEL, id, id, unsigned long long))objc_msgSend)(mgr, sel, title, subtitle, total);
}

void KSActivityUpdate(double progress, NSString *detail)
{
    id mgr = KSActivityShared();
    if (!mgr) return;
    SEL sel = NSSelectorFromString(@"updateWithProgress:detail:");
    if (![mgr respondsToSelector:sel]) return;
    ((void (*)(id, SEL, double, id))objc_msgSend)(mgr, sel, progress, detail);
}

void KSActivityFinish(BOOL success, NSString *message)
{
    id mgr = KSActivityShared();
    if (!mgr) return;
    SEL sel = NSSelectorFromString(@"finishWithSuccess:message:");
    if (![mgr respondsToSelector:sel]) return;
    ((void (*)(id, SEL, BOOL, id))objc_msgSend)(mgr, sel, success, message);
}

BOOL KSActivitySupported(void)
{
    id mgr = KSActivityShared();
    if (!mgr) return NO;
    SEL sel = NSSelectorFromString(@"supported");
    if (![mgr respondsToSelector:sel]) return NO;
    return ((BOOL (*)(id, SEL))objc_msgSend)(mgr, sel);
}

