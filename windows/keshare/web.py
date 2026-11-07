# -*- coding: utf-8 -*-
"""局域网 HTTP / WebSocket 文件通道（和 CTL/DATA 共用 47377 端口）。

为什么单独做一条通道
--------------------
热点只是"让两台设备在同一网段"的一种办法。只要在同一局域网（同一个路由器、
有线、公司 Wi-Fi 都算），对端用浏览器打开 ``http://<本机IP>:47377/`` 就能收发文
件，**不需要装 KeShare，也不需要开热点**。WebSocket 通道则给以后做网页端/其他
语言客户端留了接口，同样不依赖热点。

路由（HTTP/1.1，明文）
----------------------
``GET  /``            上传 + 下载页面（HTML）
``GET  /api/info``    设备信息 JSON
``GET  /f/<rel>``     下载共享目录里的文件，支持 ``Range``（断点续传）
``HEAD /f/<rel>``     只取头部（大小/类型）
``PUT  /f/<name>``    上传到接收目录，``Content-Length`` 必需
``GET  /ws``          WebSocket 升级（自研最小 RFC6455）

WebSocket 子协议（自研，v1）
---------------------------
文本帧是 JSON 控制消息，二进制帧是数据块（复用 KS DATA 帧格式）::

    -> {"t":"hello","v":1}
    <- {"t":"hello_ack","v":1,"name":"...","version":"...","share":"<相对路径>"}

    -> {"t":"get","path":"a/b.txt","offset":0}
    <- {"t":"get_resp","ok":true,"size":N,"name":"b.txt"}
    <- <binary: seq|offset|len|payload> ... <binary: len=0 结束>
    <- {"t":"hash","id":"..","hash":"<sha256>","size":N}

    -> {"t":"put","name":"x.bin","size":N}
    <- {"t":"put_ready","id":".."}
    -> <binary 块 ...>  <binary: len=0>
    <- {"t":"done","id":"..","size":N,"hash":"<sha256>","name":"x.bin"}
    <- {"t":"err","err":"<code>","msg":"..."}

二进制块格式与 DATA 通道完全一致：``>IQI`` + payload。
"""

import base64
import hashlib
import html
import json
import os
import socket
import struct
import time

from . import files, protocol

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEADER = 16 * 1024
MAX_WS_FRAME = 8 * 1024 * 1024
CHUNK = 256 * 1024

HTTP_METHOD_PREFIXES = (b"GET ", b"PUT ", b"POST", b"HEAD", b"OPTI", b"DELE", b"PATC")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
class HttpError(Exception):
    def __init__(self, code, reason="error", message=""):
        Exception.__init__(self, message or reason)
        self.code = int(code)
        self.reason = reason
        self.message = message or reason


def looks_like_http(first4):
    """给 server 的分流用：前 4 字节是否像 HTTP 方法。"""
    if not first4:
        return False
    return any(first4 == prefix or prefix.startswith(first4) for prefix in HTTP_METHOD_PREFIXES)


def _is_private_ip(text):
    try:
        import ipaddress
        ip = ipaddress.ip_address(str(text).strip())
    except Exception:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _json_response(payload, code=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return code, "application/json; charset=utf-8", body


def _text_response(text, code=200, ctype="text/plain; charset=utf-8"):
    return code, ctype, text.encode("utf-8")


def _read_until(sock, marker, initial=b"", limit=MAX_HEADER):
    buf = bytearray(initial)
    while marker not in buf:
        if len(buf) > limit:
            raise HttpError(413, "header_too_large")
        block = sock.recv(4096)
        if not block:
            raise HttpError(400, "incomplete")
        buf += block
    index = buf.index(marker) + len(marker)
    return bytes(buf[:index]), bytes(buf[index:])


def _parse_request(head):
    try:
        text = head.decode("iso-8859-1")
    except Exception:
        raise HttpError(400, "bad_request")
    lines = text.split("\r\n")
    if not lines:
        raise HttpError(400, "bad_request")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        raise HttpError(400, "bad_request")
    method, target = parts[0].upper(), parts[1]
    version = parts[2] if len(parts) > 2 else "HTTP/1.1"
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return method, target, version, headers


def _parse_range(value, total):
    """``bytes=start-end`` -> (start, end)；不合法返回 None。"""
    if not value or not value.lower().startswith("bytes="):
        return None
    spec = value.split("=", 1)[1].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_text, end_text = spec.split("-", 1)
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else total - 1
        else:
            # bytes=-N 表示最后 N 字节
            length = int(end_text)
            start = max(0, total - length)
            end = total - 1
    except ValueError:
        return None
    if start < 0 or end < start or start >= total:
        return None
    return start, min(end, total - 1)


# ---------------------------------------------------------------------------
# 响应
# ---------------------------------------------------------------------------
def _send(sock, code, ctype, body, extra_headers=None, head_only=False):
    reason = {
        200: "OK", 206: "Partial Content", 400: "Bad Request", 403: "Forbidden",
        404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
        500: "Internal Server Error",
    }.get(code, "OK")
    lines = [
        "HTTP/1.1 %d %s" % (code, reason),
        "Content-Type: %s" % (ctype,),
        "Content-Length: %d" % (len(body),),
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Methods: GET, PUT, HEAD, OPTIONS",
        "Access-Control-Allow-Headers: Content-Type, Range",
        "Cache-Control: no-store",
        "Connection: close",
    ]
    for key, value in (extra_headers or {}).items():
        lines.append("%s: %s" % (key, value))
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
    sock.sendall(raw)
    if not head_only and body:
        sock.sendall(body)


def _stream_file(sock, path, start, end, total):
    length = end - start + 1
    header = (
        "HTTP/1.1 206 Partial Content\r\n"
        "Content-Type: application/octet-stream\r\n"
        "Content-Length: %d\r\n"
        "Content-Range: bytes %d-%d/%d\r\n"
        "Accept-Ranges: bytes\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n\r\n"
        % (length, start, end, total)
    )
    sock.sendall(header.encode("iso-8859-1"))
    remaining = length
    with open(path, "rb") as handle:
        handle.seek(start)
        while remaining > 0:
            block = handle.read(min(CHUNK, remaining))
            if not block:
                break
            sock.sendall(block)
            remaining -= len(block)


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
_PAGE = u"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KeShare · __NAME__</title>
<style>
:root{color-scheme:dark}
body{margin:0;font:15px/1.5 -apple-system,"Segoe UI","Microsoft YaHei UI",sans-serif;
  color:#eef2ff;background:radial-gradient(120% 120% at 20% 0%,#1b2440 0,#0b0f1a 60%)}
main{max-width:760px;margin:0 auto;padding:22px 16px 60px}
.card{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);
  border-radius:22px;padding:18px;margin-bottom:14px;backdrop-filter:blur(18px)}
h1{font-size:19px;margin:0 0 4px}
p.sub{margin:0;color:#98a4c0;font-size:12.5px}
.drop{border:1.5px dashed rgba(140,190,255,.5);border-radius:18px;padding:26px 16px;
  text-align:center;color:#9fb2d6}
.drop.hot{border-color:#5cb8ff;background:rgba(92,184,255,.10)}
button{font:inherit;padding:10px 18px;border-radius:14px;border:0;cursor:pointer;
  background:#5cb8ff;color:#06101f;font-weight:600}
input[type=file]{display:none}
ul{list-style:none;margin:0;padding:0}
li{display:flex;align-items:center;gap:10px;padding:11px 4px;
  border-bottom:1px solid rgba(255,255,255,.07)}
li:last-child{border-bottom:0}
a{color:#8fd0ff;text-decoration:none;word-break:break-all}
small{color:#7d89a6}
.bar{height:6px;border-radius:6px;background:rgba(255,255,255,.12);overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;width:0;background:#5cb8ff;transition:width .15s}
</style></head><body><main>
<div class="card">
  <h1>KeShare · __NAME__</h1>
  <p class="sub">局域网直连 · __VERSION__ · <span id="stat">__COUNT__ 个共享文件</span></p>
</div>
<div class="card">
  <div class="drop" id="drop">
    <p style="margin:0 0 12px">把文件拖到这里，或点下面的按钮</p>
    <button id="pick">选择文件上传</button>
    <input type="file" id="file" multiple>
    <div class="bar" id="barwrap" style="display:none"><i id="bar"></i></div>
    <p class="sub" id="prog" style="margin-top:8px"></p>
  </div>
</div>
<div class="card">
  <p class="sub" style="margin-bottom:8px">__DIRLABEL__</p>
  <ul>__LIST__</ul>
</div>
</main>
<script>
const drop=document.getElementById('drop'),file=document.getElementById('file'),
  bar=document.getElementById('bar'),barwrap=document.getElementById('barwrap'),
  prog=document.getElementById('prog'),stat=document.getElementById('stat');
let busy=false;
async function upload(list){
  if(busy||!list.length)return; busy=true; barwrap.style.display='block';
  for(const f of list){
    prog.textContent='正在上传 '+f.name+' …'; bar.style.width='0%';
    try{
      const res=await fetch('/f/'+encodeURIComponent(f.name),{method:'PUT',
        headers:{'Content-Type':'application/octet-stream'},body:f,
        // 让浏览器把进度报出来
        duplex:'half'});
      const data=await res.json();
      if(data.ok){prog.textContent='已上传 '+data.name; location.reload();}
      else{prog.textContent='失败: '+(data.err||'unknown');}
    }catch(e){prog.textContent='失败: '+e;}
  }
  busy=false;
}
document.getElementById('pick').onclick=e=>{e.preventDefault();file.click();};
file.onchange=e=>upload(e.target.files);
['dragenter','dragover'].forEach(t=>drop.addEventListener(t,e=>{
  e.preventDefault();drop.classList.add('hot');}));
['dragleave','drop'].forEach(t=>drop.addEventListener(t,e=>{
  e.preventDefault();drop.classList.remove('hot');}));
drop.addEventListener('drop',e=>upload(e.dataTransfer.files));
</script></body></html>
"""


def _render_page(name, version, entries, rel):
    rows = []
    if rel:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        rows.append('<li><a href="/?d=%s">← 上一级</a></li>' % html.escape(parent, quote=True))
    for entry in entries:
        label = html.escape(entry["name"])
        href = "/f/" + html.escape(entry["path"], quote=True)
        if entry["kind"] == "dir":
            rows.append('<li>📁 <a href="/?d=%s">%s</a></li>'
                        % (html.escape(entry["path"], quote=True), label))
        else:
            rows.append('<li>📄 <a href="%s" download>%s</a>'
                        ' <small>%s</small></li>'
                        % (href, label, _human(entry.get("size") or 0)))
    if not rows:
        rows.append('<li><small>这里还没有文件</small></li>')
    page = (_PAGE
            .replace("__NAME__", html.escape(name or "KeShare"))
            .replace("__VERSION__", html.escape(version or ""))
            .replace("__COUNT__", str(len(entries)))
            .replace("__LIST__", "".join(rows))
            .replace("__DIRLABEL__", "/" + html.escape(rel) if rel else "共享目录"))
    return page


def _human(size):
    try:
        size = float(size)
    except (TypeError, ValueError):
        return "-"
    for unit, limit in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if size >= limit:
            return "%.1f %s" % (size / limit, unit)
    return "%d B" % int(size)


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------
def serve_http(sock, first, addr, config, log, emit=None):
    """处理一条 HTTP 连接；``first`` 是已经读掉的 4 字节。"""
    try:
        sock.settimeout(300.0)      # 大文件上传/下载不能沿用 15s 的连接超时
    except OSError:
        pass
    peer_ip = addr[0] if addr else ""
    if not _is_private_ip(peer_ip):
        _send(sock, 403, "text/plain; charset=utf-8",
              "KeShare 只允许局域网访问".encode("utf-8"))
        return
    if not getattr(config, "web_access", True):
        _send(sock, 403, "text/plain; charset=utf-8",
              "局域网 HTTP 通道已在设置里关闭".encode("utf-8"))
        return

    head, rest = _read_until(sock, b"\r\n\r\n", initial=first)
    try:
        method, target, version, headers = _parse_request(head)
    except HttpError as exc:
        _send(sock, exc.code, "text/plain", exc.reason.encode())
        return

    path, _, query = target.partition("?")
    query_map = {}
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        query_map[_unquote(key)] = _unquote(value)

    if method == "OPTIONS":
        _send(sock, 200, "text/plain", b"", {"Content-Length": "0"})
        return

    # ---- WebSocket 升级 ----
    if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
        _serve_ws(sock, headers, config, log, emit, peer_ip, rest)
        return

    try:
        if method in ("GET", "HEAD") and path in ("/", "/index.html"):
            rel = files.normalise_rel(query_map.get("d", ""))
            listing = files.ShareRoot(config.share_root).list_dir(rel, 500, 0)
            page = _render_page(config.device_name, _version(), listing["files"], rel)
            _send(sock, 200, "text/html; charset=utf-8", page.encode("utf-8"),
                  head_only=(method == "HEAD"))
            return

        if method == "GET" and path == "/api/info":
            listing = files.ShareRoot(config.share_root).list_dir("", 500, 0)
            code, ctype, body = _json_response({
                "name": config.device_name, "version": _version(),
                "port": config.port, "files": len(listing["files"]),
                "protocol": "KS/1 + HTTP + WS/1",
            })
            _send(sock, code, ctype, body)
            return

        if path.startswith("/f/"):
            rel = files.normalise_rel(_unquote(path[3:]), allow_empty=False)
            root = files.ShareRoot(config.share_root)
            target_path = root.resolve(rel)
            if os.path.isdir(target_path):
                listing = root.list_dir(rel, 500, 0)
                page = _render_page(config.device_name, _version(), listing["files"], rel)
                _send(sock, 200, "text/html; charset=utf-8", page.encode("utf-8"))
                return

            if method in ("GET", "HEAD"):
                if not os.path.isfile(target_path):
                    _send(sock, 404, "text/plain", b"not found")
                    return
                total = os.path.getsize(target_path)
                rng = _parse_range(headers.get("range"), total)
                if method == "HEAD":
                    _send(sock, 200, "application/octet-stream", b"", {
                        "Content-Length": str(total),
                        "Accept-Ranges": "bytes",
                    }, head_only=True)
                    return
                if rng is not None:
                    _stream_file(sock, target_path, rng[0], rng[1], total)
                    log.info("HTTP 下载（Range %d-%d/%d）%s <- %s"
                             % (rng[0], rng[1], total, rel, peer_ip))
                    return
                _stream_file(sock, target_path, 0, max(0, total - 1), total)
                log.info("HTTP 下载 %s（%d 字节）-> %s" % (rel, total, peer_ip))
                if emit:
                    emit("transfer", {"state": "done", "direction": "out",
                                      "name": os.path.basename(target_path),
                                      "size": total, "channel": "http"})
                return

            if method == "PUT":
                _handle_put(sock, rel, headers, config, log, emit, peer_ip, rest)
                return

        _send(sock, 404, "text/plain; charset=utf-8", "未知路径".encode("utf-8"))
    except files.SandboxError:
        _send(sock, 403, "text/plain; charset=utf-8", "路径越权".encode("utf-8"))
    except FileNotFoundError:
        _send(sock, 404, "text/plain; charset=utf-8", "文件不存在".encode("utf-8"))
    except (OSError, HttpError) as exc:
        log.warn("HTTP 处理失败 (%s %s): %s" % (method, path, exc))
        try:
            _send(sock, 500, "text/plain; charset=utf-8", ("失败: %s" % (exc,)).encode("utf-8"))
        except OSError:
            pass


def _handle_put(sock, rel, headers, config, log, emit, peer_ip, rest):
    length_text = headers.get("content-length")
    if length_text is None:
        _send(sock, 400, "text/plain", b"Content-Length required")
        return
    try:
        length = int(length_text)
    except ValueError:
        _send(sock, 400, "text/plain", b"bad Content-Length")
        return
    if length < 0 or length > int(getattr(config, "max_file_bytes", 2 * 1024 ** 3)):
        _send(sock, 413, "application/json", b'{"ok":false,"err":"too_large"}')
        return

    config.ensure_dirs()
    safe_name = files.safe_filename(os.path.basename(rel) or "upload.bin")
    dest = files.unique_path(config.receive_dir, safe_name)
    part = dest + ".part"

    digest = hashlib.sha256()
    received = 0
    try:
        with open(part, "wb") as handle:
            pending = rest
            while pending:
                block = pending[:65536]
                handle.write(block)
                digest.update(block)
                received += len(block)
                pending = pending[65536:]
            while received < length:
                block = sock.recv(min(65536, length - received))
                if not block:
                    break
                handle.write(block)
                digest.update(block)
                received += len(block)
        if received != length:
            raise OSError("短上传 %d/%d" % (received, length))
        os.replace(part, dest)
    except Exception as exc:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
        _send(sock, 500, "application/json",
              json.dumps({"ok": False, "err": "io", "msg": str(exc)}).encode("utf-8"))
        return

    payload = {"ok": True, "name": os.path.basename(dest), "size": received,
               "hash": digest.hexdigest(), "dest": dest}
    _send(sock, 200, "application/json; charset=utf-8",
          json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    log.info("HTTP 上传 %s（%d 字节）<- %s" % (os.path.basename(dest), received, peer_ip))
    if emit:
        emit("transfer", {"state": "done", "direction": "in",
                          "name": os.path.basename(dest), "size": received,
                          "channel": "http"})


def _unquote(text):
    try:
        from urllib.parse import unquote
        return unquote(text or "")
    except Exception:
        return text or ""


def _version():
    try:
        from . import KSAPP_DISPLAY
        return KSAPP_DISPLAY
    except Exception:
        return "0.0.1"


# ---------------------------------------------------------------------------
# WebSocket（最小 RFC6455 实现）
# ---------------------------------------------------------------------------
def _ws_send(sock, opcode, payload=b""):
    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    sock.sendall(bytes(header) + payload)


def _ws_send_text(sock, obj):
    _ws_send(sock, 0x1, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _read_exact(sock, count):
    buf = b""
    while len(buf) < count:
        block = sock.recv(count - len(buf))
        if not block:
            raise OSError("连接断开")
        buf += block
    return buf


def _ws_recv(sock):
    """返回 (opcode, payload)；自动处理分片与控制帧。"""
    fragments = []
    total_opcode = None
    while True:
        head = _read_exact(sock, 2)
        fin = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", _read_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _read_exact(sock, 8))[0]
        if length > MAX_WS_FRAME:
            raise OSError("WS 帧过大")
        mask = _read_exact(sock, 4) if masked else b""
        payload = _read_exact(sock, length) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

        if opcode == 0x8:                       # close
            return 0x8, payload
        if opcode == 0x9:                       # ping
            _ws_send(sock, 0xA, payload)
            continue
        if opcode == 0xA:                       # pong
            continue
        if opcode in (0x1, 0x2):
            total_opcode = opcode
        fragments.append(payload)
        if fin:
            return total_opcode or 0x2, b"".join(fragments)


def _serve_ws(sock, headers, config, log, emit, peer_ip, rest):
    key = headers.get("sec-websocket-key", "")
    if not key:
        _send(sock, 400, "text/plain", b"missing Sec-WebSocket-Key")
        return
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
    handshake = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n"
        "Sec-WebSocket-Protocol: keshare.v1\r\n\r\n" % accept
    )
    sock.sendall(handshake.encode("ascii"))
    sock.settimeout(300.0)
    log.info("WebSocket 通道接入 %s" % (peer_ip,))

    outstanding = {}          # id -> (file handle, dest, hasher, size)
    if rest:
        log.debug("WS 握手后还有 %d 字节缓存（忽略）" % len(rest))

    try:
        while True:
            opcode, payload = _ws_recv(sock)
            if opcode == 0x8:
                break
            if opcode == 0x2:
                if not outstanding:
                    _ws_send_text(sock, {"t": "err", "err": "no_pending",
                                         "msg": "先发 put 再发数据块"})
                    continue
                tid = next(iter(outstanding))
                handle, dest, hasher, size = outstanding[tid]
                frame = _decode_chunk(payload)
                if frame is None:
                    _ws_send_text(sock, {"t": "err", "err": "bad_frame"})
                    continue
                seq, offset, data = frame
                if not data:                       # 结束帧
                    handle.close()
                    os.replace(dest + ".part", dest)
                    digest = hasher.hexdigest()
                    total = os.path.getsize(dest)
                    _ws_send_text(sock, {"t": "done", "id": tid, "size": total,
                                         "hash": digest,
                                         "name": os.path.basename(dest)})
                    log.info("WS 上传完成 %s（%d 字节）" % (os.path.basename(dest), total))
                    if emit:
                        emit("transfer", {"state": "done", "direction": "in",
                                          "name": os.path.basename(dest), "size": total,
                                          "channel": "ws"})
                    outstanding.pop(tid, None)
                    continue
                handle.write(data)
                hasher.update(data)
                continue

            try:
                message = json.loads(payload.decode("utf-8"))
            except Exception:
                _ws_send_text(sock, {"t": "err", "err": "bad_json"})
                continue
            kind = message.get("t")
            if kind == "hello":
                _ws_send_text(sock, {
                    "t": "hello_ack", "v": 1, "name": config.device_name,
                    "version": _version(), "protocol": "kws/1",
                    "share": True, "upload": bool(getattr(config, "allow_upload", True)),
                })
            elif kind == "get":
                _ws_get(sock, message, config, log, emit)
            elif kind == "put":
                name = files.safe_filename(str(message.get("name") or "upload.bin"))
                size = int(message.get("size") or 0)
                if size < 0 or size > int(getattr(config, "max_file_bytes", 2 ** 31)):
                    _ws_send_text(sock, {"t": "err", "err": "too_large"})
                    continue
                config.ensure_dirs()
                dest = files.unique_path(config.receive_dir, name)
                handle = open(dest + ".part", "wb")
                tid = "%08x" % (int(time.time() * 1000) & 0xFFFFFFFF)
                outstanding[tid] = (handle, dest, hashlib.sha256(), size)
                _ws_send_text(sock, {"t": "put_ready", "id": tid})
            else:
                _ws_send_text(sock, {"t": "err", "err": "unsupported",
                                     "msg": str(kind)})
    except (OSError, struct.error) as exc:
        log.debug("WS 连接结束 %s: %s" % (peer_ip, exc))
    finally:
        for tid, item in list(outstanding.items()):
            try:
                item[0].close()
            except Exception:
                pass
            try:
                os.remove(item[1] + ".part")
            except OSError:
                pass
        outstanding.clear()


def _decode_chunk(payload):
    if len(payload) < 16:
        return None
    seq, offset, length = struct.unpack(">IQI", payload[:16])
    if length > protocol.CHUNK_MAX:
        return None
    body = payload[16:16 + length]
    if len(body) != length:
        return None
    return seq, offset, body


def _ws_get(sock, message, config, log, emit):
    rel = files.normalise_rel(str(message.get("path") or ""), allow_empty=False)
    root = files.ShareRoot(config.share_root)
    try:
        target = root.resolve(rel)
    except files.SandboxError:
        _ws_send_text(sock, {"t": "get_resp", "ok": False, "err": "denied"})
        return
    if not os.path.isfile(target):
        _ws_send_text(sock, {"t": "get_resp", "ok": False, "err": "notfound"})
        return
    size = os.path.getsize(target)
    offset = max(0, min(int(message.get("offset") or 0), size))
    _ws_send_text(sock, {"t": "get_resp", "ok": True, "size": size,
                         "name": os.path.basename(target), "offset": offset})

    digest = hashlib.sha256()
    seq = 0
    sent = offset
    with open(target, "rb") as handle:
        handle.seek(offset)
        if offset:
            # 断点续传：先把已发部分的摘要算出来，保证 hash 描述整份文件
            head = handle.read(offset)
            digest.update(head)
            handle.seek(offset)
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
            frame = struct.pack(">IQI", seq, sent, len(block)) + block
            _ws_send(sock, 0x2, frame)
            sent += len(block)
            seq += 1
    _ws_send(sock, 0x2, struct.pack(">IQI", seq, sent, 0))
    _ws_send_text(sock, {"t": "hash", "id": str(message.get("id") or ""),
                         "hash": digest.hexdigest(), "size": size})
    log.info("WS 下载 %s（%d 字节）" % (rel, size - offset))
    if emit:
        emit("transfer", {"state": "done", "direction": "out",
                          "name": os.path.basename(target), "size": size,
                          "channel": "ws"})
