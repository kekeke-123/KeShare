//
//  KSActivityShim.m -- 实时活动已移除（用户要求不适配灵动岛）
//  保留同名 C 接口，调用点无需修改。
//

#import "KeShare.h"

void KSActivityStart(NSString *title, NSString *subtitle, unsigned long long total)
{
    (void)title; (void)subtitle; (void)total;
}

void KSActivityUpdate(double progress, NSString *detail)
{
    (void)progress; (void)detail;
}

void KSActivityFinish(BOOL success, NSString *message)
{
    (void)success; (void)message;
}

BOOL KSActivitySupported(void)
{
    return NO;
}