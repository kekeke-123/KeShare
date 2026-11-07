# -*- coding: utf-8 -*-
"""假 iOS 端：严格按 PROTOCOL.md 实现，用于在 Windows 上做端到端联调。

它能做的事：
  * UDP 47377 每隔 5 秒广播 announce（让 Windows 的发现能搜到它）
  * TCP 47377 监听，按首字节分流：
        "CTL "   -> 控制会话（hello / hello_ack / list / stat / get / put_offer / hash）
        "DATA "  -> 数据通道（本端作为发送方写帧，或作为接收方读帧）
        其它     -> 控制会话（裸长度帧）

用法：
    python tools/fake_ios.py --root D:\\tmp\\fake-share --name "iPhone-测试"
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keshare import protocol, ke1024  # noqa: E402

HOST = "0.0.0.0"
PORT = protocol.KS_PORT


def _sha(data):
    return ke1024.sha_ke_1024(data).hex()


class FakeIOS(object):
    def __init__(self, root, name="iPhone", did="FAKEIOS00000001", port=PORT):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.name = name
        self.did = did
        self.port = port
        self.token = ""
        self.served = {}      # id -> (abs path, size)  本端待发送
        self.ctl_sock = None
        self.peer_host = "127.0.0.1"
        self.peer_port = 47377
        self.incoming = {}    # id -> temp path          本端待接收
        self.stop_flag = False

    # ---------------------------------------------------------------- UDP
    def announce_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not self.stop_flag:
            payload = json.dumps({
                "ks": 1, "t": "announce", "did": self.did, "name": self.name,
                "kind": "ios", "osv": "17.0", "port": self.port,
                "caps": 0x7f, "host": "127.0.0.1",
            }).encode("utf-8")
            try:
                sock.sendto(payload, ("255.255.255.255", PORT))
                sock.sendto(payload, ("127.0.0.1", PORT))
            except OSError:
                pass
            time.sleep(5)
        sock.close()

    # ---------------------------------------------------------------- CTL
    def send_msg(self, sock, message):
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        sock.sendall(struct.pack(">I", len(data)) + data)

    def recv_msg(self, sock):
        head = self._read_exactly(sock, 4)
        if not head:
            return None
        (length,) = struct.unpack(">I", head)
        if length == 0 or length > 65536:
            return None
        body = self._read_exactly(sock, length)
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def _read_exactly(sock, count):
        buf = b""
        while len(buf) < count:
            chunk = sock.recv(count - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf

    def ctl_session(self, sock, remote_ip):
        sock.settimeout(120)
        self.ctl_sock = sock
        while not self.stop_flag:
            msg = self.recv_msg(sock)
            if msg is None:
                break
            t = msg.get("t")
            print("[fake-ios] <-", t, json.dumps(msg, ensure_ascii=False)[:120])

            if t == "hello":
                self.peer_host = msg.get("host") or remote_ip
                self.peer_port = int(msg.get("port") or 47377)
                self.token = _sha(("KS1-CTL\0" + msg.get("did", "") + msg.get("nonce", "") +
                                   self.did + "0011223344556677").encode())[:16]
                self.send_msg(sock, {
                    "t": "hello_ack", "v": 1, "did": self.did, "name": self.name,
                    "kind": "ios", "osv": "17.0", "port": self.port, "caps": 0x7f,
                    "nonce": "0011223344556677", "token": self.token,
                    "host": remote_ip,
                })
            elif t == "hello_ack":
                self.token = msg.get("token", "")
            elif t == "list":
                rel = msg.get("path") or ""
                target = os.path.join(self.root, rel) if rel else self.root
                files = []
                try:
                    for entry in sorted(os.listdir(target)):
                        full = os.path.join(target, entry)
                        isdir = os.path.isdir(full)
                        files.append({
                            "path": os.path.join(rel, entry).replace("\\", "/"),
                            "name": entry,
                            "size": 0 if isdir else os.path.getsize(full),
                            "mtime": int(os.path.getmtime(full)),
                            "kind": "dir" if isdir else "file",
                        })
                except OSError:
                    self.send_msg(sock, {"t": "list_resp", "ok": False, "err": "notfound"})
                    continue
                self.send_msg(sock, {"t": "list_resp", "ok": True, "files": files})
            elif t == "stat":
                rel = msg.get("path") or ""
                full = os.path.join(self.root, rel)
                if not os.path.isfile(full):
                    self.send_msg(sock, {"t": "stat_resp", "ok": False, "err": "notfound"})
                    continue
                with open(full, "rb") as fh:
                    digest = _sha(fh.read())
                self.send_msg(sock, {"t": "stat_resp", "ok": True, "path": rel,
                                     "size": os.path.getsize(full),
                                     "mtime": int(os.path.getmtime(full)), "hash": digest})
            elif t == "get":
                rel = msg.get("path") or ""
                full = os.path.join(self.root, rel)
                if not os.path.isfile(full):
                    self.send_msg(sock, {"t": "get_resp", "ok": False,
                                         "id": msg.get("id", ""), "err": "notfound"})
                    continue
                tid = msg.get("id", "")
                self.served[tid] = (full, os.path.getsize(full))
                self.send_msg(sock, {"t": "get_resp", "ok": True, "id": tid,
                                     "size": os.path.getsize(full),
                                     "name": os.path.basename(full)})
            elif t == "put_offer":
                tid = msg.get("id", "")
                tmp = os.path.join(self.root, ".recv-%s.part" % tid)
                self.incoming[tid] = tmp
                self.send_msg(sock, {"t": "put_resp", "ok": True, "id": tid})
            elif t == "hash":
                tid = msg.get("id", "")
                want = (msg.get("hash") or msg.get("sha_ke_1024") or "").lower()
                tmp = self.incoming.get(tid)
                ok = False
                if tmp and os.path.isfile(tmp):
                    with open(tmp, "rb") as fh:
                        got = _sha(fh.read())
                    ok = (got == want)
                    if ok:
                        final = os.path.join(self.root, "recv-%s.bin" % tid)
                        os.replace(tmp, final)
                    else:
                        os.remove(tmp)
                self.send_msg(sock, {"t": "hash_resp", "id": tid, "ok": ok})
            elif t == "bye":
                break
            else:
                self.send_msg(sock, {"t": "err", "err": "unsupported"})
        try:
            sock.close()
        except OSError:
            pass

    # ---------------------------------------------------------------- DATA
    def data_session(self, sock, tid):
        """本端作为发送方：把文件写成帧，然后经 CTL 声明摘要。"""
        entry = self.served.get(tid)
        if not entry:
            sock.close()
            return
        path, size = entry
        chunk = 64 * 1024
        seq = 0
        off = 0
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                sock.sendall(struct.pack(">IQI", seq, off, len(block)) + block)
                seq += 1
                off += len(block)
        sock.sendall(struct.pack(">IQI", seq, off, 0))
        sock.close()
        print("[fake-ios] 数据已发出 %d 字节" % off)

        with open(path, "rb") as fh:
            digest = _sha(fh.read())
        # 必须在同一条 CTL 会话上发送 hash，客户端才会收到
        try:
            if self.ctl_sock is not None:
                self.send_msg(self.ctl_sock, {"t": "hash", "id": tid, "hash": digest, "size": size})
                print("[fake-ios] 已发送 hash %s..." % digest[:16])
        except Exception as exc:
            print("[fake-ios] 发送 hash 失败:", exc)
        return digest

    def receive_data(self, tid, tmp_path):
        """作为接收方：连接发送方的 DATA 端口并读取帧。"""
        try:
            sock = socket.create_connection((self.peer_host, self.peer_port), timeout=10)
            sock.sendall(("DATA %s %s\n" % (self.token or "-", tid)).encode("ascii"))
            total = 0
            with open(tmp_path, "wb") as fh:
                while True:
                    head = self._read_exactly(sock, 16)
                    if len(head) < 16:
                        break
                    seq, off, length = struct.unpack(">IQI", head)
                    if length == 0:
                        break
                    body = self._read_exactly(sock, length)
                    if len(body) < length:
                        break
                    fh.write(body)
                    total += length
            sock.close()
            print("[fake-ios] 接收到 %d 字节 (id=%s)" % (total, tid))
        except Exception as exc:
            print("[fake-ios] 接收失败:", exc)
    # ---------------------------------------------------------------- 主循环
    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, self.port))
        srv.listen(8)
        print("[fake-ios] 监听 %s:%d，共享目录 %s" % (HOST, self.port, self.root))
        while not self.stop_flag:
            try:
                conn, addr = srv.accept()
            except OSError:
                break
            threading.Thread(target=self.handle, args=(conn, addr[0]), daemon=True).start()
        srv.close()

    def handle(self, sock, remote_ip):
        try:
            first = sock.recv(5, socket.MSG_PEEK)
        except OSError:
            sock.close()
            return
        if first.startswith(b"DATA "):
            line = b""
            while not line.endswith(b"\n"):
                ch = sock.recv(1)
                if not ch:
                    sock.close()
                    return
                line += ch
            parts = line.decode("utf-8", "replace").split()
            tid = parts[2] if len(parts) >= 3 else ""
            self.data_session(sock, tid)

            return
        if first.startswith(b"CTL "):
            line = b""
            while not line.endswith(b"\n"):
                ch = sock.recv(1)
                if not ch:
                    sock.close()
                    return
                line += ch
        self.ctl_session(sock, remote_ip)

    def push_hash_via_ctl(self, digest, tid):
        """用一条新 CTL 连接补发 hash（模拟真实设备在既有链路上发送）。"""
        try:
            sock = socket.create_connection(("127.0.0.1", self.ctl_peer_port), timeout=5)
            sock.sendall(b"CTL -\n")
            self.send_msg(sock, {"t": "hello", "v": 1, "did": self.did, "name": self.name,
                                 "kind": "ios", "osv": "17.0", "port": self.port,
                                 "caps": 0x7f, "nonce": "aabbccddeeff0011"})
            self.recv_msg(sock)
            self.send_msg(sock, {"t": "hash", "id": tid, "hash": digest,
                                 "size": os.path.getsize(self.served[tid][0])})
            reply = self.recv_msg(sock)
            print("[fake-ios] hash_resp:", reply)
            sock.close()
        except Exception as exc:
            print("[fake-ios] 补发 hash 失败:", exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.path.join(os.getcwd(), "fake-share"))
    parser.add_argument("--name", default="iPhone-测试机")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    app = FakeIOS(args.root, args.name, port=args.port)
    app.ctl_peer_port = args.port
    threading.Thread(target=app.announce_loop, daemon=True).start()
    app.serve()


if __name__ == "__main__":
    main()
