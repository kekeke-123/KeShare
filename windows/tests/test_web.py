# -*- coding: utf-8 -*-
"""局域网 HTTP / WebSocket 通道测试（不需要 KeShare 客户端，直接当浏览器用）。

覆盖：页面 / api/info / 下载 / Range 断点 / 中文名 / 空文件 / HEAD 单 Content-Length
      / 上传落盘与哈希 / 上传禁用 / WebSocket hello·get·put / close 回帧。
"""

import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request

WINDOWS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WINDOWS_ROOT not in sys.path:
    sys.path.insert(0, WINDOWS_ROOT)

from keshare import config as ksconfig
from keshare import web
from keshare.log import Log
from keshare.server import KSServer


def temp_dir(prefix="ks-web-"):
    for base in (tempfile.gettempdir(), os.environ.get("TMP"),
                 os.environ.get("TEMP"),
                 os.path.join(os.path.dirname(WINDOWS_ROOT), ".scratch", "tmp")):
        if not base:
            continue
        try:
            if not os.path.isdir(base):
                os.makedirs(base)
            return tempfile.mkdtemp(prefix=prefix, dir=base)
        except OSError:
            continue
    raise RuntimeError("没有可写临时目录")


class WebChannelTests(unittest.TestCase):
    allow_upload = True

    def setUp(self):
        self.folder = temp_dir()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.share = os.path.join(self.folder, "share")
        os.makedirs(self.share)
        self.blob = os.urandom(300 * 1024 + 777)
        with open(os.path.join(self.share, "big.bin"), "wb") as handle:
            handle.write(self.blob)
        open(os.path.join(self.share, "empty.bin"), "wb").close()
        with open(os.path.join(self.share, "中文 名字.txt"), "w", encoding="utf-8") as handle:
            handle.write("内容")

        cfg = ksconfig.Config({
            "device": {"name": "网页机", "id": "W" * 16, "secret": "dd" * 32},
            "share": {"root": self.share, "receive_dir": "Received",
                      "allow_upload": self.allow_upload},
            "network": {"port": 0},
        }, os.path.join(self.folder, "w.json"))
        cfg.ensure_dirs()
        self.cfg = cfg
        self.log = Log(path=os.path.join(self.folder, "w.log"), ring_size=4000)
        self.server = KSServer(cfg, self.log)
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    # -- helpers -------------------------------------------------------
    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def raw(self, path, method="GET", headers=None, body=b""):
        request = "%s %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n" % (method, path)
        for key, value in (headers or {}).items():
            request += "%s: %s\r\n" % (key, value)
        request += "\r\n"
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        sock.sendall(request.encode("iso-8859-1") + body)
        chunks = []
        while True:
            block = sock.recv(65536)
            if not block:
                break
            chunks.append(block)
        sock.close()
        return b"".join(chunks)

    def ws_connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(("GET /ws HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n" % key).encode())
        handshake = b""
        while b"\r\n\r\n" not in handshake:
            block = sock.recv(1024)
            if not block:
                raise AssertionError("WS 握手断开")
            handshake += block
        self.assertIn(b"101 Switching Protocols", handshake)
        return sock

    def ws_send(self, sock, opcode, data):
        header = bytearray([0x80 | opcode])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        sock.sendall(bytes(header) + payload)

    def ws_recv(self, sock):
        def read_exact(n):
            buf = b""
            while len(buf) < n:
                block = sock.recv(n - len(buf))
                if not block:
                    raise AssertionError("WS 连接断开")
                buf += block
            return buf

        head = read_exact(2)
        opcode = head[0] & 0x0F
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", read_exact(8))[0]
        return opcode, (read_exact(length) if length else b"")

    # -- HTTP ----------------------------------------------------------
    def test_page_and_info(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("KeShare".encode(), body)
        info = json.loads(self._get("/api/info")[2])
        self.assertEqual(info["name"], "网页机")
        self.assertIn("HTTP", info["protocol"])

    def test_download_full_and_range(self):
        status, headers, body = self._get("/f/big.bin")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), len(self.blob))
        self.assertEqual(hashlib.sha256(body).hexdigest(),
                         hashlib.sha256(self.blob).hexdigest())

        status, headers, body = self._get("/f/big.bin", {"Range": "bytes=1000-"})
        self.assertEqual(status, 206)
        self.assertIn("bytes 1000-", headers.get("Content-Range", ""))
        self.assertEqual(body, self.blob[1000:])

    def test_head_has_single_content_length(self):
        response = self.raw("/f/big.bin", method="HEAD")
        head = response.partition(b"\r\n\r\n")[0]
        self.assertEqual(head.count(b"Content-Length"), 1)
        self.assertIn(("Content-Length: %d" % len(self.blob)).encode(), head)

    def test_empty_file(self):
        status, headers, body = self._get("/f/empty.bin")
        self.assertEqual((status, body), (200, b""))
        response = self.raw("/f/empty.bin", method="HEAD")
        head = response.partition(b"\r\n\r\n")[0]
        self.assertEqual(head.count(b"Content-Length"), 1)

    def test_chinese_name(self):
        path = "/f/" + urllib.parse.quote("中文 名字.txt")
        status, headers, body = self._get(path)
        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), "内容")

    def test_upload_when_allowed(self):
        payload = os.urandom(64 * 1024)
        request = urllib.request.Request(self.base + "/f/up.bin", data=payload,
                                         method="PUT",
                                         headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read())
        self.assertTrue(result["ok"])
        self.assertEqual(result["size"], len(payload))
        self.assertEqual(result["hash"], hashlib.sha256(payload).hexdigest())
        saved = os.path.join(self.cfg.receive_dir, "up.bin")
        self.assertTrue(os.path.isfile(saved))

    def test_unknown_path(self):
        response = self.raw("/nope")
        self.assertTrue(response.startswith(b"HTTP/1.1 404"))

    def test_private_address_policy(self):
        self.assertTrue(web._is_private_ip("192.168.1.9"))
        self.assertTrue(web._is_private_ip("10.0.0.3"))
        self.assertTrue(web._is_private_ip("127.0.0.1"))
        self.assertFalse(web._is_private_ip("8.8.8.8"))

    # -- WebSocket -----------------------------------------------------
    def test_ws_hello_and_get(self):
        sock = self.ws_connect()
        try:
            self.ws_send(sock, 0x1, json.dumps({"t": "hello"}).encode())
            opcode, data = self.ws_recv(sock)
            self.assertEqual(opcode, 0x1)
            self.assertEqual(json.loads(data)["protocol"], "kws/1")

            self.ws_send(sock, 0x1, json.dumps(
                {"t": "get", "path": "big.bin", "id": "t1"}).encode())
            opcode, data = self.ws_recv(sock)
            self.assertEqual(json.loads(data)["ok"], True)
            received = bytearray()
            digest = None
            while True:
                opcode, frame = self.ws_recv(sock)
                if opcode == 0x1:
                    message = json.loads(frame)
                    if message.get("t") == "hash":
                        digest = message["hash"]
                        break
                    continue
                _seq, _offset, length = struct.unpack(">IQI", frame[:16])
                if length:
                    received += frame[16:16 + length]
            self.assertEqual(bytes(received), self.blob)
            self.assertEqual(digest, hashlib.sha256(self.blob).hexdigest())
        finally:
            sock.close()

    def test_ws_put_and_close(self):
        payload = os.urandom(200 * 1024)
        sock = self.ws_connect()
        try:
            self.ws_send(sock, 0x1, json.dumps(
                {"t": "put", "name": "ws-up.bin", "size": len(payload)}).encode())
            opcode, data = self.ws_recv(sock)
            ready = json.loads(data)
            self.assertEqual(ready["t"], "put_ready")

            seq = offset = 0
            for start in range(0, len(payload), 64 * 1024):
                block = payload[start:start + 64 * 1024]
                self.ws_send(sock, 0x2, struct.pack(">IQI", seq, offset, len(block)) + block)
                seq += 1
                offset += len(block)
            self.ws_send(sock, 0x2, struct.pack(">IQI", seq, offset, 0))

            done = None
            while True:
                opcode, data = self.ws_recv(sock)
                if opcode == 0x1:
                    message = json.loads(data)
                    if message.get("t") == "done":
                        done = message
                        break
            self.assertEqual(done["size"], len(payload))
            self.assertEqual(done["hash"], hashlib.sha256(payload).hexdigest())
            self.assertTrue(os.path.isfile(os.path.join(self.cfg.receive_dir, "ws-up.bin")))

            self.ws_send(sock, 0x8, struct.pack(">H", 1000))
            opcode, _ = self.ws_recv(sock)
            self.assertEqual(opcode, 0x8)
        finally:
            sock.close()

    # -- 内部 ----------------------------------------------------------
    def _get(self, path, headers=None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers), response.read()


class WebChannelUploadDisabledTests(unittest.TestCase):
    """上传被关掉时，HTTP PUT 和 WS put 都必须被拒。"""

    def setUp(self):
        self.folder = temp_dir()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.share = os.path.join(self.folder, "share")
        os.makedirs(self.share)
        cfg = ksconfig.Config({
            "device": {"name": "禁传机", "id": "N" * 16, "secret": "ff" * 32},
            "share": {"root": self.share, "receive_dir": "Received",
                      "allow_upload": False},
            "network": {"port": 0},
        }, os.path.join(self.folder, "n.json"))
        cfg.ensure_dirs()
        self.cfg = cfg
        self.log = Log(path=os.path.join(self.folder, "n.log"), ring_size=1000)
        self.server = KSServer(cfg, self.log)
        self.port = self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_put_denied(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/f/up.bin" % self.port, data=b"abc", method="PUT")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 403)
        self.assertFalse(os.path.exists(os.path.join(self.cfg.receive_dir, "up.bin")))

    def test_ws_put_denied(self):
        helper = WebChannelTests("test_unknown_path")
        helper.port = self.port
        sock = helper.ws_connect()
        try:
            helper.ws_send(sock, 0x1, json.dumps(
                {"t": "put", "name": "x.bin", "size": 10}).encode())
            opcode, data = helper.ws_recv(sock)
            self.assertEqual(opcode, 0x1)
            self.assertEqual(json.loads(data)["err"], "denied")
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
