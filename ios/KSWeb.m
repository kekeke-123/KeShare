//
//  KSWeb.m -- 局域网 HTTP / WebSocket 文件通道（与 CTL/DATA 共用 47377）
//
//  为什么单独做一条通道
//  --------------------
//  热点只是“让两台设备在同一网段”的一种办法。只要在同一局域网（同一个路由器、
//  有线、公司 Wi-Fi 都算），对端用浏览器打开 http://<本机IP>:47377/ 就能收发文
//  件，不需要装 KeShare，也不需要开热点。WebSocket 通道给以后的网页端/其他语言
//  客户端留接口，同样不依赖热点。
//
//  路由（HTTP/1.1，明文）
//  ---------------------
//    GET  /            上传 + 下载页面
//    GET  /api/info    设备信息 JSON
//    GET  /f/<rel>     下载共享目录文件，支持 Range（断点续传）
//    HEAD /f/<rel>     只取头部
//    PUT  /f/<name>    上传到接收目录（Content-Length 必需）
//    GET  /ws          WebSocket 升级（自研最小 RFC6455）
//
//  WebSocket 子协议（自研，v1）
//  ---------------------------
//    文本帧是 JSON 控制消息，二进制帧是数据块（复用 KS DATA 帧格式 >IQI + payload）
//    -> {"t":"hello","v":1}            <- {"t":"hello_ack",...}
//    -> {"t":"get","path":"a.txt"}     <- get_resp + 二进制块 + hash
//    -> {"t":"put","name":"x","size":N} <- put_ready
//    -> 二进制块 ... 结束帧(0)          <- {"t":"done","hash":...}
//

#import "KeShare.h"
#import "KSInternal.h"
#import <CommonCrypto/CommonDigest.h>
#import <sys/socket.h>
#import <sys/time.h>
#import <unistd.h>
#import <fcntl.h>

#define KS_WEB_MAX_HEADER   (16 * 1024)
#define KS_WEB_MAX_WS_FRAME (8 * 1024 * 1024)
#define KS_WEB_CHUNK        (256 * 1024)
#define KS_WS_GUID          @"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

#pragma mark - 小工具

static NSString *KSWebURLDecode(NSString *text)
{
    return [text stringByRemovingPercentEncoding] ?: text;
}

static NSString *KSWebHumanSize(unsigned long long size)
{
    if (size >= 1024ULL * 1024 * 1024) return [NSString stringWithFormat:@"%.1f GB", size / 1073741824.0];
    if (size >= 1024ULL * 1024) return [NSString stringWithFormat:@"%.1f MB", size / 1048576.0];
    if (size >= 1024) return [NSString stringWithFormat:@"%.1f KB", size / 1024.0];
    return [NSString stringWithFormat:@"%llu B", size];
}

static NSString *KSWebHTMLEscape(NSString *text)
{
    NSMutableString *out = [text mutableCopy] ?: [NSMutableString string];
    [out replaceOccurrencesOfString:@"&" withString:@"&amp;" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"<" withString:@"&lt;" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@">" withString:@"&gt;" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"\"" withString:@"&quot;" options:0 range:NSMakeRange(0, out.length)];
    return out;
}

/// 读 socket 直到 marker（含），返回 (header, 多余字节)
static NSData *KSWebReadUntil(int fd, NSData *initial, NSData *marker, NSData **rest)
{
    NSMutableData *buf = [initial mutableCopy] ?: [NSMutableData data];
    uint8_t tmp[4096];
    while (YES) {
        NSRange range = [buf rangeOfData:marker options:0
                                   range:NSMakeRange(0, buf.length)];
        if (range.location != NSNotFound) {
            NSUInteger end = range.location + range.length;
            NSData *head = [buf subdataWithRange:NSMakeRange(0, end)];
            if (rest) {
                *rest = end < buf.length
                    ? [buf subdataWithRange:NSMakeRange(end, buf.length - end)]
                    : [NSData data];
            }
            return head;
        }
        if (buf.length > KS_WEB_MAX_HEADER) return nil;
        ssize_t n = read(fd, tmp, sizeof tmp);
        if (n <= 0) return nil;
        [buf appendBytes:tmp length:(NSUInteger)n];
    }
}

static void KSWebSendHeader(int fd, int code, NSString *reason, NSString *ctype,
                            long long length, NSArray<NSString *> *extra)
{
    NSMutableString *head = [NSMutableString string];
    [head appendFormat:@"HTTP/1.1 %d %@\r\n", code, reason ?: @"OK"];
    [head appendFormat:@"Content-Type: %@\r\n", ctype ?: @"text/plain; charset=utf-8"];
    [head appendFormat:@"Content-Length: %lld\r\n", length];
    [head appendString:@"Access-Control-Allow-Origin: *\r\n"];
    [head appendString:@"Access-Control-Allow-Methods: GET, PUT, HEAD, OPTIONS\r\n"];
    [head appendString:@"Access-Control-Allow-Headers: Content-Type, Range\r\n"];
    [head appendString:@"Cache-Control: no-store\r\n"];
    [head appendString:@"Connection: close\r\n"];
    for (NSString *line in extra ?: @[]) {
        [head appendFormat:@"%@\r\n", line];
    }
    [head appendString:@"\r\n"];
    NSData *data = [head dataUsingEncoding:NSUTF8StringEncoding];
    KSWriteFull(fd, data.bytes, data.length);
}

static void KSWebSendText(int fd, int code, NSString *ctype, NSString *text)
{
    NSData *body = [(text ?: @"") dataUsingEncoding:NSUTF8StringEncoding];
    KSWebSendHeader(fd, code, code == 200 ? @"OK" : @"Error", ctype, (long long)body.length, nil);
    if (body.length) KSWriteFull(fd, body.bytes, body.length);
}

static void KSWebSendJSON(int fd, int code, NSDictionary *payload)
{
    NSData *body = [NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
    KSWebSendHeader(fd, code, code == 200 ? @"OK" : @"Error",
                    @"application/json; charset=utf-8", (long long)body.length, nil);
    if (body.length) KSWriteFull(fd, body.bytes, body.length);
}

#pragma mark - 页面

static NSString *KSWebPage(NSString *name, NSString *version,
                           NSArray<NSDictionary *> *entries, NSString *rel)
{
    NSMutableString *rows = [NSMutableString string];
    if (rel.length) {
        NSString *parent = [rel containsString:@"/"]
            ? [rel substringToIndex:[rel rangeOfString:@"/" options:NSBackwardsSearch].location]
            : @"";
        [rows appendFormat:@"<li><a href=\"/?d=%@\">← 上一级</a></li>",
         KSWebHTMLEscape(parent)];
    }
    for (NSDictionary *entry in entries) {
        NSString *entryName = KSWebHTMLEscape(entry[@"name"] ?: @"");
        NSString *path = KSWebHTMLEscape(entry[@"path"] ?: @"");
        if ([entry[@"kind"] isEqualToString:@"dir"]) {
            [rows appendFormat:@"<li>📁 <a href=\"/?d=%@\">%@</a></li>", path, entryName];
        } else {
            [rows appendFormat:@"<li>📄 <a href=\"/f/%@\" download>%@</a> <small>%@</small></li>",
             path, entryName,
             KSWebHumanSize((unsigned long long)[entry[@"size"] unsignedLongLongValue])];
        }
    }
    if (rows.length == 0) [rows appendString:@"<li><small>这里还没有文件</small></li>"];

    NSString *html =
    @"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    @"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    @"<title>KeShare · %@</title><style>"
    @":root{color-scheme:dark}body{margin:0;font:15px/1.5 -apple-system,\"PingFang SC\",sans-serif;"
    @"color:#eef2ff;background:radial-gradient(120%% 120%% at 20%% 0%%,#1b2440 0,#0b0f1a 60%%)}"
    @"main{max-width:760px;margin:0 auto;padding:22px 16px 60px}"
    @".card{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);"
    @"border-radius:22px;padding:18px;margin-bottom:14px;-webkit-backdrop-filter:blur(18px)}"
    @"h1{font-size:19px;margin:0 0 4px}p.sub{margin:0;color:#98a4c0;font-size:12.5px}"
    @".drop{border:1.5px dashed rgba(140,190,255,.5);border-radius:18px;padding:26px 16px;"
    @"text-align:center;color:#9fb2d6}"
    @"button{font:inherit;padding:10px 18px;border-radius:14px;border:0;cursor:pointer;"
    @"background:#5cb8ff;color:#06101f;font-weight:600}"
    @"input[type=file]{display:none}ul{list-style:none;margin:0;padding:0}"
    @"li{display:flex;gap:10px;align-items:center;padding:11px 4px;"
    @"border-bottom:1px solid rgba(255,255,255,.07)}li:last-child{border-bottom:0}"
    @"a{color:#8fd0ff;text-decoration:none;word-break:break-all}small{color:#7d89a6}"
    @"</style></head><body><main>"
    @"<div class=\"card\"><h1>KeShare · %@</h1>"
    @"<p class=\"sub\">局域网直连 · %@ · %lu 个文件</p></div>"
    @"<div class=\"card\"><div class=\"drop\"><p>选文件上传到这台 iPhone</p>"
    @"<button id=\"pick\">选择文件上传</button>"
    @"<input type=\"file\" id=\"file\" multiple>"
    @"<p class=\"sub\" id=\"prog\" style=\"margin-top:10px\"></p></div></div>"
    @"<div class=\"card\"><p class=\"sub\">%@</p><ul>%@</ul></div>"
    @"</main><script>"
    @"const f=document.getElementById('file'),prog=document.getElementById('prog');"
    @"document.getElementById('pick').onclick=e=>{e.preventDefault();f.click();};"
    @"f.onchange=async e=>{for(const x of e.target.files){prog.textContent='上传 '+x.name+' …';"
    @"try{const r=await fetch('/f/'+encodeURIComponent(x.name),{method:'PUT',body:x});"
    @"const d=await r.json();prog.textContent=d.ok?('已上传 '+d.name):('失败 '+d.err);"
    @"if(d.ok)location.reload();}catch(err){prog.textContent='失败 '+err;}}};"
    @"</script></body></html>";

    return [NSString stringWithFormat:html,
            KSWebHTMLEscape(name ?: @"KeShare"),
            KSWebHTMLEscape(name ?: @"KeShare"),
            KSWebHTMLEscape(version ?: @""),
            (unsigned long)entries.count,
            rel.length ? [NSString stringWithFormat:@"/%@", KSWebHTMLEscape(rel)] : @"共享目录",
            rows];
}

/// 列一层目录；返回 [{path,name,size,mtime,kind}]
static NSArray<NSDictionary *> *KSWebListDir(NSString *dir, NSString *rel)
{
    NSMutableArray *out = [NSMutableArray array];
    NSArray *names = [[NSFileManager defaultManager] contentsOfDirectoryAtPath:dir error:NULL] ?: @[];
    names = [names sortedArrayUsingSelector:@selector(localizedStandardCompare:)];
    for (NSString *name in names) {
        NSString *full = [dir stringByAppendingPathComponent:name];
        NSDictionary *attr = [[NSFileManager defaultManager] attributesOfItemAtPath:full error:NULL];
        BOOL isDir = [attr[NSFileType] isEqual:NSFileTypeDirectory];
        NSMutableDictionary *entry = [@{
            @"path": rel.length ? [rel stringByAppendingPathComponent:name] : name,
            @"name": name,
            @"kind": isDir ? @"dir" : @"file",
            @"mtime": @((long long)[attr[NSFileModificationDate] timeIntervalSince1970])
        } mutableCopy];
        if (!isDir) entry[@"size"] = @([attr[NSFileSize] unsignedLongLongValue]);
        [out addObject:entry];
    }
    return out;
}

#pragma mark - 下载 / Range

static BOOL KSWebStreamFile(int fd, NSString *path, long long start, long long end, long long total)
{
    NSString *range = [NSString stringWithFormat:@"Content-Range: bytes %lld-%lld/%lld", start, end, total];
    KSWebSendHeader(fd, 206, @"Partial Content", @"application/octet-stream",
                    end - start + 1,
                    @[@"Accept-Ranges: bytes", range]);
    int file = open(path.fileSystemRepresentation, O_RDONLY);
    if (file < 0) return NO;
    lseek(file, start, SEEK_SET);
    long long remaining = end - start + 1;
    uint8_t *buf = malloc(KS_WEB_CHUNK);
    if (!buf) { close(file); return NO; }
    BOOL ok = YES;
    while (remaining > 0 && ok) {
        ssize_t n = read(file, buf, (size_t)MIN((long long)KS_WEB_CHUNK, remaining));
        if (n <= 0) { ok = NO; break; }
        KSWriteFull(fd, buf, (size_t)n);
        remaining -= n;
    }
    free(buf);
    close(file);
    return ok;
}

static BOOL KSWebParseRange(NSString *value, long long total, long long *start, long long *end)
{
    if (![value hasPrefix:@"bytes="]) return NO;
    NSString *spec = [[value substringFromIndex:6] componentsSeparatedByString:@","].firstObject;
    spec = [spec stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
    NSArray *parts = [spec componentsSeparatedByString:@"-"];
    if (parts.count != 2) return NO;
    long long a = [parts[0] longLongValue];
    long long b = [parts[1] longLongValue];
    if (parts[0].length == 0) {                 // bytes=-N
        if (b <= 0) return NO;
        *start = MAX(0, total - b);
        *end = total - 1;
    } else {
        *start = a;
        *end = parts[1].length ? b : total - 1;
    }
    if (*start < 0 || *end < *start || *start >= total) return NO;
    if (*end > total - 1) *end = total - 1;
    return YES;
}

#pragma mark - WebSocket

static void KSWSFrame(int fd, uint8_t opcode, const uint8_t *payload, NSUInteger length)
{
    uint8_t head[10];
    NSUInteger headLen = 0;
    head[headLen++] = (uint8_t)(0x80 | (opcode & 0x0F));
    if (length < 126) {
        head[headLen++] = (uint8_t)length;
    } else if (length < 65536) {
        head[headLen++] = 126;
        head[headLen++] = (uint8_t)(length >> 8);
        head[headLen++] = (uint8_t)length;
    } else {
        head[headLen++] = 127;
        for (int k = 7; k >= 0; k--) head[headLen++] = (uint8_t)((uint64_t)length >> (8 * k));
    }
    KSWriteFull(fd, head, headLen);
    if (length) KSWriteFull(fd, payload, length);
}

static void KSWSJSON(int fd, NSDictionary *message)
{
    NSData *json = [NSJSONSerialization dataWithJSONObject:message options:0 error:NULL];
    if (json.length) KSWSFrame(fd, 0x1, json.bytes, json.length);
}

static BOOL KSWSReadExact(int fd, uint8_t *buf, size_t length)
{
    return KSReadFull(fd, buf, length);
}

/// 返回 opcode；payload 通过 outData 返回（自动处理分片与控制帧）
static int KSWSRecv(int fd, NSMutableData *outData)
{
    [outData setLength:0];
    int messageOpcode = 0;
    while (YES) {
        uint8_t head[2];
        if (!KSWSReadExact(fd, head, 2)) return -1;
        BOOL fin = (head[0] & 0x80) != 0;
        uint8_t opcode = head[0] & 0x0F;
        BOOL masked = (head[1] & 0x80) != 0;
        uint64_t length = head[1] & 0x7F;
        if (length == 126) {
            uint8_t ext[2];
            if (!KSWSReadExact(fd, ext, 2)) return -1;
            length = ((uint64_t)ext[0] << 8) | ext[1];
        } else if (length == 127) {
            uint8_t ext[8];
            if (!KSWSReadExact(fd, ext, 8)) return -1;
            length = 0;
            for (int k = 0; k < 8; k++) length = (length << 8) | ext[k];
        }
        if (length > KS_WEB_MAX_WS_FRAME) return -1;
        uint8_t mask[4] = {0, 0, 0, 0};
        if (masked && !KSWSReadExact(fd, mask, 4)) return -1;
        NSMutableData *payload = [NSMutableData dataWithLength:(NSUInteger)length];
        if (length && !KSWSReadExact(fd, payload.mutableBytes, (size_t)length)) return -1;
        if (masked && length) {
            uint8_t *bytes = payload.mutableBytes;
            for (NSUInteger i = 0; i < length; i++) bytes[i] ^= mask[i % 4];
        }
        if (opcode == 0x8) return 0x8;
        if (opcode == 0x9) { KSWSFrame(fd, 0xA, payload.bytes, payload.length); continue; }
        if (opcode == 0xA) continue;
        if (opcode == 0x1 || opcode == 0x2) messageOpcode = opcode;
        [outData appendData:payload];
        if (fin) return messageOpcode ?: 0x2;
    }
}

#pragma mark - WS 业务

static void KSWSHandleGet(int fd, NSDictionary *message)
{
    NSString *rel = KSWebURLDecode([message[@"path"] description] ?: @"");
    if (!KSIsValidRelativePath(rel)) {
        KSWSJSON(fd, @{@"t": @"get_resp", @"ok": @NO, @"err": @"denied"});
        return;
    }
    NSString *target = [KSConfig.shared.shareRoot stringByAppendingPathComponent:rel];
    BOOL isDir = NO;
    if (![[NSFileManager defaultManager] fileExistsAtPath:target isDirectory:&isDir] || isDir) {
        KSWSJSON(fd, @{@"t": @"get_resp", @"ok": @NO, @"err": @"notfound"});
        return;
    }
    unsigned long long total = KSFileSize(target);
    unsigned long long offset = [message[@"offset"] unsignedLongLongValue];
    if (offset > total) offset = 0;
    KSWSJSON(fd, @{@"t": @"get_resp", @"ok": @YES, @"size": @(total),
                   @"name": target.lastPathComponent, @"offset": @(offset)});

    int file = open(target.fileSystemRepresentation, O_RDONLY);
    if (file < 0) return;
    uint8_t *buf = malloc(KS_WEB_CHUNK);
    if (!buf) { close(file); return; }

    // WS 上没有增量摘要，整份文件算一次 SHA-256（和 DATA 通道的 hash 字段一致）
    NSString *digest = KSHashOfFile(target, NULL) ?: @"";
    uint32_t seq = 0;
    unsigned long long sent = offset;
    lseek(file, (off_t)offset, SEEK_SET);
    while (YES) {
        ssize_t n = read(file, buf, KS_WEB_CHUNK);
        if (n <= 0) break;
        uint8_t head[16];
        head[0] = (uint8_t)(seq >> 24); head[1] = (uint8_t)(seq >> 16);
        head[2] = (uint8_t)(seq >> 8);  head[3] = (uint8_t)seq;
        for (int k = 0; k < 8; k++) head[4 + k] = (uint8_t)(sent >> (56 - 8 * k));
        uint32_t len = (uint32_t)n;
        head[12] = (uint8_t)(len >> 24); head[13] = (uint8_t)(len >> 16);
        head[14] = (uint8_t)(len >> 8);  head[15] = (uint8_t)len;
        NSMutableData *frame = [NSMutableData dataWithBytes:head length:16];
        [frame appendBytes:buf length:(NSUInteger)n];
        KSWSFrame(fd, 0x2, frame.bytes, frame.length);
        sent += (unsigned long long)n;
        seq++;
    }
    free(buf);
    close(file);
    uint8_t end[16] = {0};
    end[0] = (uint8_t)(seq >> 24); end[1] = (uint8_t)(seq >> 16);
    end[2] = (uint8_t)(seq >> 8);  end[3] = (uint8_t)seq;
    for (int k = 0; k < 8; k++) end[4 + k] = (uint8_t)(sent >> (56 - 8 * k));
    KSWSFrame(fd, 0x2, end, sizeof end);
    KSWSJSON(fd, @{@"t": @"hash", @"id": [message[@"id"] description] ?: @"",
                   @"hash": digest, @"size": @(total)});
    [[KSLog shared] info:@"WS 下载 %@（%llu 字节）", rel, total - offset];
}

#pragma mark - 主入口

BOOL KSWebLooksLikeHTTP(NSString *firstLine)
{
    if (!firstLine.length) return NO;
    for (NSString *prefix in @[@"GET ", @"PUT ", @"POST", @"HEAD", @"OPTI", @"DELE", @"PATC"]) {
        if ([firstLine hasPrefix:prefix]) return YES;
    }
    return NO;
}

void KSWebServe(int fd, NSString *firstLine, NSString *peerIp, void (^emit)(NSDictionary *info))
{
    struct timeval tv = { 300, 0 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);

    NSData *initial = [firstLine dataUsingEncoding:NSUTF8StringEncoding] ?: [NSData data];
    NSData *rest = nil;
    NSData *headData = KSWebReadUntil(fd, initial, [@"\r\n\r\n" dataUsingEncoding:NSUTF8StringEncoding], &rest);
    if (!headData) return;

    NSString *head = [[NSString alloc] initWithData:headData encoding:NSUTF8StringEncoding] ?: @"";
    NSArray<NSString *> *lines = [head componentsSeparatedByString:@"\r\n"];
    if (lines.count == 0) return;
    NSArray<NSString *> *requestParts = [lines[0] componentsSeparatedByString:@" "];
    if (requestParts.count < 2) return;
    NSString *method = requestParts[0];
    NSString *target = requestParts[1];
    NSMutableDictionary *headers = [NSMutableDictionary dictionary];
    for (NSUInteger i = 1; i < lines.count; i++) {
        NSString *line = lines[i];
        NSRange colon = [line rangeOfString:@":"];
        if (colon.location == NSNotFound) continue;
        NSString *key = [[line substringToIndex:colon.location] lowercaseString];
        NSString *value = [[line substringFromIndex:colon.location + 1]
                           stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
        if (key.length) headers[key] = value;
    }

    if (!KSConfig.shared.webAccess) {
        KSWebSendText(fd, 403, @"text/plain; charset=utf-8", @"局域网网页传输已在设置里关闭");
        return;
    }

    NSArray *targetParts = [target componentsSeparatedByString:@"?"];
    NSString *path = targetParts.firstObject ?: @"/";
    NSMutableDictionary *query = [NSMutableDictionary dictionary];
    if (targetParts.count > 1) {
        for (NSString *pair in [targetParts[1] componentsSeparatedByString:@"&"]) {
            NSRange eq = [pair rangeOfString:@"="];
            if (eq.location == NSNotFound) continue;
            query[KSWebURLDecode([pair substringToIndex:eq.location])] =
                KSWebURLDecode([pair substringFromIndex:eq.location + 1]);
        }
    }

    if ([method isEqualToString:@"OPTIONS"]) {
        KSWebSendHeader(fd, 200, @"OK", @"text/plain", 0, nil);
        return;
    }

    // ---- WebSocket ----
    if ([path isEqualToString:@"/ws"] &&
        [[headers[@"upgrade"] lowercaseString] isEqualToString:@"websocket"]) {
        NSString *key = headers[@"sec-websocket-key"];
        if (!key.length) {
            KSWebSendText(fd, 400, @"text/plain", @"missing Sec-WebSocket-Key");
            return;
        }
        NSString *joined = [key stringByAppendingString:KS_WS_GUID];
        NSData *sha = nil;
        {
            unsigned char digest[CC_SHA1_DIGEST_LENGTH];
            NSData *raw = [joined dataUsingEncoding:NSUTF8StringEncoding];
            CC_SHA1(raw.bytes, (CC_LONG)raw.length, digest);
            sha = [NSData dataWithBytes:digest length:CC_SHA1_DIGEST_LENGTH];
        }
        NSString *accept = [sha base64EncodedStringWithOptions:0];
        NSString *handshake = [NSString stringWithFormat:
            @"HTTP/1.1 101 Switching Protocols\r\n"
            @"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            @"Sec-WebSocket-Accept: %@\r\n"
            @"Sec-WebSocket-Protocol: keshare.v1\r\n\r\n", accept];
        NSData *hs = [handshake dataUsingEncoding:NSUTF8StringEncoding];
        KSWriteFull(fd, hs.bytes, hs.length);
        [[KSLog shared] info:@"WebSocket 通道接入 %@", peerIp ?: @"-"];

        NSMutableDictionary *pending = [NSMutableDictionary dictionary];   // tid -> @[fd, dest, path]
        NSMutableData *payload = [NSMutableData data];
        while (YES) {
            @autoreleasepool {
                int opcode = KSWSRecv(fd, payload);
                if (opcode < 0 || opcode == 0x8) break;
                if (opcode == 0x2) {
                    NSString *tid = pending.allKeys.firstObject;
                    if (!tid) {
                        KSWSJSON(fd, @{@"t": @"err", @"err": @"no_pending"});
                        continue;
                    }
                    NSArray *item = pending[tid];
                    int out = [item[0] intValue];
                    if (payload.length < 16) continue;
                    const uint8_t *b = payload.bytes;
                    uint32_t len = ((uint32_t)b[12] << 24) | ((uint32_t)b[13] << 16) |
                                   ((uint32_t)b[14] << 8) | b[15];
                    if (len == 0) {
                        close(out);
                        NSString *part = [item[1] stringByAppendingString:@".part"];
                        NSString *dest = item[1];
                        [[NSFileManager defaultManager] removeItemAtPath:dest error:NULL];
                        [[NSFileManager defaultManager] moveItemAtPath:part toPath:dest error:NULL];
                        NSString *digest = KSHashOfFile(dest, NULL) ?: @"";
                        unsigned long long size = KSFileSize(dest);
                        KSWSJSON(fd, @{@"t": @"done", @"id": tid, @"size": @(size),
                                       @"hash": digest, @"name": dest.lastPathComponent});
                        [[KSLog shared] info:@"WS 上传完成 %@（%llu 字节）",
                            dest.lastPathComponent, size];
                        if (emit) emit(@{@"state": @"done", @"direction": @"in",
                                         @"name": dest.lastPathComponent,
                                         @"size": @(size), @"channel": @"ws"});
                        [pending removeObjectForKey:tid];
                        continue;
                    }
                    if (len <= payload.length - 16) {
                        NSData *chunk = [payload subdataWithRange:NSMakeRange(16, len)];
                        if (write(out, chunk.bytes, chunk.length) != (ssize_t)chunk.length) {
                            KSWSJSON(fd, @{@"t": @"err", @"err": @"io"});
                        }
                    }
                    continue;
                }

                id obj = [NSJSONSerialization JSONObjectWithData:payload options:0 error:NULL];
                if (![obj isKindOfClass:[NSDictionary class]]) {
                    KSWSJSON(fd, @{@"t": @"err", @"err": @"bad_json"});
                    continue;
                }
                NSDictionary *message = obj;
                NSString *kind = message[@"t"];
                if ([kind isEqualToString:@"hello"]) {
                    KSWSJSON(fd, @{@"t": @"hello_ack", @"v": @1,
                                   @"name": KSConfig.shared.deviceName ?: @"iPhone",
                                   @"version": KS_VERSION_STRING,
                                   @"protocol": @"kws/1", @"share": @YES,
                                   @"upload": @(KSConfig.shared.allowUpload)});
                } else if ([kind isEqualToString:@"get"]) {
                    KSWSHandleGet(fd, message);
                } else if ([kind isEqualToString:@"put"]) {
                    NSString *name = message[@"name"] ?: @"upload.bin";
                    if (!KSIsValidRelativePath(name)) {
                        KSWSJSON(fd, @{@"t": @"err", @"err": @"denied"});
                        continue;
                    }
                    [[NSFileManager defaultManager] createDirectoryAtPath:KSConfig.shared.receivePath
                                              withIntermediateDirectories:YES attributes:nil error:NULL];
                    NSString *dest = KSUniquePathInDirectory(KSConfig.shared.receivePath, name);
                    int out = open([[dest stringByAppendingString:@".part"] fileSystemRepresentation],
                                   O_CREAT | O_TRUNC | O_WRONLY, 0644);
                    if (out < 0) {
                        KSWSJSON(fd, @{@"t": @"err", @"err": @"io"});
                        continue;
                    }
                    NSString *tid = KSRandomHex(4);
                    pending[tid] = @[@(out), dest];
                    KSWSJSON(fd, @{@"t": @"put_ready", @"id": tid});
                } else {
                    KSWSJSON(fd, @{@"t": @"err", @"err": @"unsupported"});
                }
            }
        }
        for (NSString *tid in pending) {
            NSArray *item = pending[tid];
            close([item[0] intValue]);
            [[NSFileManager defaultManager] removeItemAtPath:
                [item[1] stringByAppendingString:@".part"] error:NULL];
        }
        return;
    }

    // ---- 页面 ----
    if ([method isEqualToString:@"GET"] || [method isEqualToString:@"HEAD"]) {
        if ([path isEqualToString:@"/"] || [path isEqualToString:@"/index.html"]) {
            NSString *rel = KSWebURLDecode(query[@"d"] ?: @"");
            if (rel.length && !KSIsValidRelativePath(rel)) {
                KSWebSendText(fd, 403, @"text/plain; charset=utf-8", @"路径越权");
                return;
            }
            NSString *dir = rel.length
                ? [KSConfig.shared.shareRoot stringByAppendingPathComponent:rel]
                : KSConfig.shared.shareRoot;
            NSArray *entries = KSWebListDir(dir, rel);
            NSString *page = KSWebPage(KSConfig.shared.deviceName, KS_VERSION_STRING, entries, rel);
            NSData *body = [page dataUsingEncoding:NSUTF8StringEncoding];
            KSWebSendHeader(fd, 200, @"OK", @"text/html; charset=utf-8",
                            (long long)body.length, nil);
            if (![method isEqualToString:@"HEAD"]) KSWriteFull(fd, body.bytes, body.length);
            return;
        }
        if ([path isEqualToString:@"/api/info"]) {
            KSWebSendJSON(fd, 200, @{
                @"name": KSConfig.shared.deviceName ?: @"iPhone",
                @"version": KS_VERSION_STRING,
                @"port": @(KSConfig.shared.port),
                @"protocol": @"KS/1 + HTTP + WS/1",
            });
            return;
        }
    }

    // ---- 文件下载 / 上传 ----
    if ([path hasPrefix:@"/f/"]) {
        NSString *rel = KSWebURLDecode([path substringFromIndex:3]);
        if (!KSIsValidRelativePath(rel)) {
            KSWebSendText(fd, 403, @"text/plain; charset=utf-8", @"路径越权");
            return;
        }
        NSString *targetPath = [KSConfig.shared.shareRoot stringByAppendingPathComponent:rel];
        BOOL isDir = NO;

        if ([method isEqualToString:@"GET"] || [method isEqualToString:@"HEAD"]) {
            if ([[NSFileManager defaultManager] fileExistsAtPath:targetPath isDirectory:&isDir] && isDir) {
                NSArray *entries = KSWebListDir(targetPath, rel);
                NSString *page = KSWebPage(KSConfig.shared.deviceName, KS_VERSION_STRING, entries, rel);
                NSData *body = [page dataUsingEncoding:NSUTF8StringEncoding];
                KSWebSendHeader(fd, 200, @"OK", @"text/html; charset=utf-8",
                                (long long)body.length, nil);
                KSWriteFull(fd, body.bytes, body.length);
                return;
            }
            if (![[NSFileManager defaultManager] fileExistsAtPath:targetPath]) {
                KSWebSendText(fd, 404, @"text/plain; charset=utf-8", @"文件不存在");
                return;
            }
            long long total = (long long)KSFileSize(targetPath);
            if ([method isEqualToString:@"HEAD"]) {
                KSWebSendHeader(fd, 200, @"OK", @"application/octet-stream", total,
                                @[@"Accept-Ranges: bytes", @"Content-Length: 0"]);
                return;
            }
            long long start = 0, end = total - 1;
            if (KSWebParseRange(headers[@"range"], total, &start, &end)) {
                KSWebStreamFile(fd, targetPath, start, end, total);
                [[KSLog shared] info:@"HTTP 下载（Range %lld-%lld/%lld）%@", start, end, total, rel];
            } else {
                if (total == 0) {
                    KSWebSendHeader(fd, 200, @"OK", @"application/octet-stream", 0, nil);
                    return;
                }
                KSWebStreamFile(fd, targetPath, 0, total - 1, total);
                [[KSLog shared] info:@"HTTP 下载 %@（%lld 字节）", rel, total];
            }
            if (emit) emit(@{@"state": @"done", @"direction": @"out",
                             @"name": targetPath.lastPathComponent,
                             @"size": @(total), @"channel": @"http"});
            return;
        }

        if ([method isEqualToString:@"PUT"]) {
            NSString *lengthText = headers[@"content-length"];
            long long length = [lengthText longLongValue];
            if (length < 0 || length > (long long)KSConfig.shared.maxFileMB * 1024LL * 1024LL) {
                KSWebSendJSON(fd, 413, @{@"ok": @NO, @"err": @"too_large"});
                return;
            }
            [[NSFileManager defaultManager] createDirectoryAtPath:KSConfig.shared.receivePath
                                      withIntermediateDirectories:YES attributes:nil error:NULL];
            NSString *dest = KSUniquePathInDirectory(KSConfig.shared.receivePath,
                                                     rel.lastPathComponent ?: @"upload.bin");
            NSString *part = [dest stringByAppendingString:@".part"];
            int out = open(part.fileSystemRepresentation, O_CREAT | O_TRUNC | O_WRONLY, 0644);
            if (out < 0) {
                KSWebSendJSON(fd, 500, @{@"ok": @NO, @"err": @"io"});
                return;
            }
            long long received = 0;
            if (rest.length) {
                NSInteger take = (NSInteger)MIN((long long)rest.length, length);
                write(out, rest.bytes, (size_t)take);
                received += take;
            }
            uint8_t *buf = malloc(64 * 1024);
            BOOL ok = YES;
            while (received < length && buf) {
                ssize_t n = read(fd, buf, (size_t)MIN(65536LL, length - received));
                if (n <= 0) { ok = NO; break; }
                if (write(out, buf, (size_t)n) != n) { ok = NO; break; }
                received += n;
            }
            free(buf);
            close(out);
            if (!ok || received != length) {
                [[NSFileManager defaultManager] removeItemAtPath:part error:NULL];
                KSWebSendJSON(fd, 400, @{@"ok": @NO, @"err": @"incomplete"});
                return;
            }
            [[NSFileManager defaultManager] removeItemAtPath:dest error:NULL];
            [[NSFileManager defaultManager] moveItemAtPath:part toPath:dest error:NULL];
            NSString *digest = KSHashOfFile(dest, NULL) ?: @"";
            KSWebSendJSON(fd, 200, @{@"ok": @YES, @"name": dest.lastPathComponent,
                                     @"size": @(received), @"hash": digest, @"dest": dest});
            [[KSLog shared] info:@"HTTP 上传 %@（%lld 字节）", dest.lastPathComponent, received];
            if (emit) emit(@{@"state": @"done", @"direction": @"in",
                             @"name": dest.lastPathComponent,
                             @"size": @(received), @"channel": @"http"});
            return;
        }
    }

    KSWebSendText(fd, 404, @"text/plain; charset=utf-8", @"未知路径");
}
