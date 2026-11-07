//
//  KSResume.m -- 断点续传的持久化（收尾：3 导入发送 / 5 断点续传 共用）
//
//  存放位置：Library/Caches/KeShare/resume/  （用户不可见，符合"只暴露接收的数据"）
//    <key>.part   已写入的数据
//    <key>.json   {path,size,hash,offset,peer,direction,mtime}
//
//  清理策略：保留 7 / 30 天或永不（由 KSConfig.resumeKeepDays 决定，0 = 永不）
//

#import "KeShare.h"
#import "KSInternal.h"

static NSString *const kKSResumeDirName = @"resume";

@implementation KSResumeStore

+ (NSString *)directory
{
    NSURL *caches = [[[NSFileManager defaultManager] URLsForDirectory:NSCachesDirectory
                                                            inDomains:NSUserDomainMask] firstObject];
    NSString *dir = [[[caches URLByAppendingPathComponent:@"KeShare" isDirectory:YES]
                      URLByAppendingPathComponent:kKSResumeDirName isDirectory:YES] path];
    [[NSFileManager defaultManager] createDirectoryAtPath:dir
                              withIntermediateDirectories:YES attributes:nil error:NULL];
    return dir;
}

+ (NSString *)keyForPath:(NSString *)path size:(long long)size hash:(NSString *)hash
{
    // 同一文件同一摘要 → 同一 key，便于命中续传
    NSString *seed = [NSString stringWithFormat:@"%@|%lld|%@",
                      path ?: @"", size, hash.length ? hash : @"-"];
    return [KSSHA256HexOfData([seed dataUsingEncoding:NSUTF8StringEncoding]) substringToIndex:16];
}

+ (NSString *)partPathForKey:(NSString *)key
{
    return [[self directory] stringByAppendingPathComponent:[key stringByAppendingString:@".part"]];
}

+ (NSString *)metaPathForKey:(NSString *)key
{
    return [[self directory] stringByAppendingPathComponent:[key stringByAppendingString:@".json"]];
}

+ (NSDictionary *)entryForKey:(NSString *)key
{
    NSString *meta = [self metaPathForKey:key];
    NSData *raw = [NSData dataWithContentsOfFile:meta];
    if (!raw.length) return nil;
    NSDictionary *json = [NSJSONSerialization JSONObjectWithData:raw options:0 error:NULL];
    if (![json isKindOfClass:[NSDictionary class]]) return nil;

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *part = [self partPathForKey:key];
    if (![fm fileExistsAtPath:part]) return nil;

    NSDictionary *attrs = [fm attributesOfItemAtPath:part error:NULL];
    unsigned long long onDisk = [attrs[NSFileSize] unsignedLongLongValue];

    NSMutableDictionary *out = [json mutableCopy];
    out[@"offset"] = @(onDisk);       // 以实际字节数为准，避免元数据与实际不符
    return out;
}

+ (BOOL)saveEntry:(NSDictionary *)entry key:(NSString *)key error:(NSError **)error
{
    NSData *data = [NSJSONSerialization dataWithJSONObject:entry options:0 error:error];
    if (!data) return NO;
    return [data writeToFile:[self metaPathForKey:key] atomically:YES];
}

+ (void)removeEntryForKey:(NSString *)key
{
    NSFileManager *fm = [NSFileManager defaultManager];
    [fm removeItemAtPath:[self partPathForKey:key] error:NULL];
    [fm removeItemAtPath:[self metaPathForKey:key] error:NULL];
}

+ (NSInteger)cleanupExpiredWithKeepDays:(NSInteger)days
{
    if (days <= 0) return 0;                  // 0 = 永不清理

    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *dir = [self directory];
    NSArray *files = [fm contentsOfDirectoryAtPath:dir error:NULL] ?: @[];
    NSTimeInterval limit = days * 24 * 3600;
    NSTimeInterval now = [NSDate date].timeIntervalSince1970;
    NSInteger removed = 0;

    for (NSString *name in files) {
        if (![name hasSuffix:@".json"]) continue;
        NSString *full = [dir stringByAppendingPathComponent:name];
        NSDictionary *attrs = [fm attributesOfItemAtPath:full error:NULL];
        NSDate *mtime = attrs[NSFileModificationDate];
        if (!mtime) continue;
        if (now - mtime.timeIntervalSince1970 > limit) {
            NSString *key = [name stringByDeletingPathExtension];
            [self removeEntryForKey:key];
            removed++;
            [[KSLog shared] info:@"清理过期断点：%@", key];
        }
    }
    return removed;
}

+ (unsigned long long)totalBytes
{
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *dir = [self directory];
    NSArray *files = [fm contentsOfDirectoryAtPath:dir error:NULL] ?: @[];
    unsigned long long total = 0;
    for (NSString *name in files) {
        NSDictionary *attrs = [fm attributesOfItemAtPath:[dir stringByAppendingPathComponent:name]
                                                   error:NULL];
        total += [attrs[NSFileSize] unsignedLongLongValue];
    }
    return total;
}

@end