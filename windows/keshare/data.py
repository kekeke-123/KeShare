# -*- coding: utf-8 -*-
"""DATA channel (WiFi TCP 47377): chunked file transfer + SHA-256 checks.

PROTOCOL.md section 1.2 / 4.0:

* the **receiver** always opens the TCP connection; the sender listens and
  writes the frames,
* a connection starts with ``DATA <token> <id>\\n``,
* then ``seq:u32 | offset:u64 | len:u32 | payload`` frames follow, the final
  frame has ``len == 0`` and ``offset == total size``,
* the digest of the whole file is declared over CTL (``hash``) and checked by
  the receiver, which only then renames the ``.part`` file.

:class:`TransferRegistry` is shared by the server (which writes on accepted
DATA connections and reads on outgoing ones) and the client (which reads on its
own outgoing connection for ``get`` and registers an outbound transfer for
``put``), because both live in the same process.
"""

import os
import socket
import threading
import time

from . import hashlib_sha as filehash, protocol

CHUNK_DEFAULT = protocol.CHUNK_DEFAULT


class DataError(Exception):
    """DATA channel failure carrying a protocol error code."""

    def __init__(self, message, err="io"):
        Exception.__init__(self, message)
        self.err = err


# ---------------------------------------------------------------------------
# Socket helpers
# ---------------------------------------------------------------------------
def open_data_socket(host, port, timeout=20.0):
    """Connect to a peer's DATA listener (the receiver initiates, see 4.0)."""
    if not host:
        raise DataError("不知道对端的 WiFi 地址", "no_wifi")
    try:
        sock = socket.create_connection((str(host), int(port)), float(timeout))
    except OSError as exc:
        raise DataError("无法建立数据通道 %s:%s -- %s" % (host, port, exc), "io")
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # 1 MB 收发缓冲：局域网/热点下显著减少系统调用，吞吐能翻倍
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
    except OSError:
        pass
    return sock


def send_data_header(sock, token, transfer_id):
    """Write the one ASCII line that opens a DATA connection."""
    sock.sendall(protocol.data_header_line(token, transfer_id))


def recv_data_header(sock, max_bytes=protocol.DATA_LINE_MAX, prefix=b""):
    """Read the ``DATA <token> <id>\\n`` line; returns ``(token, id)``.

    *prefix* is used when the caller already consumed the first bytes while
    distinguishing DATA from CTL connections (the server peeks ``b"DATA"``).
    """
    buffer = bytearray(prefix or b"")
    while b"\n" not in buffer:
        if len(buffer) > max_bytes:
            raise DataError("数据通道首行过长", "badreq")
        try:
            block = sock.recv(1)
        except socket.timeout:
            raise DataError("等待数据通道首行超时", "io")
        except OSError as exc:
            raise DataError("数据通道读取失败: %s" % (exc,), "io")
        if not block:
            raise DataError("数据通道未发送首行", "badreq")
        buffer += block
    try:
        return protocol.parse_data_header(bytes(buffer))
    except protocol.ProtocolError as exc:
        raise DataError(str(exc), exc.err)


# ---------------------------------------------------------------------------
# File streaming
# ---------------------------------------------------------------------------
def send_file(sock, fileobj, size, chunk=CHUNK_DEFAULT, progress=None, rate=None,
              pause_event=None):
    """Stream *fileobj* as chunk frames and return ``{sent, frames, digest}``.

    The SHA-KE-1024 digest is computed from the bytes actually written, so the
    ``hash`` message that follows is guaranteed to describe the wire content.
    """
    chunk = max(1024, min(int(chunk or CHUNK_DEFAULT), protocol.CHUNK_MAX))
    size = int(size or 0)
    seq = 0
    sent = 0
    frames = 0
    started = time.time()
    hasher = filehash.Sha256()
    while True:
        if pause_event is not None and not pause_event.is_set():
            pause_event.wait(0.5)          # 暂停点：不消耗 CPU
            continue
        data = fileobj.read(chunk)
        if not data:
            break
        try:
            sock.sendall(protocol.encode_chunk(seq, sent, data))
        except OSError as exc:
            raise DataError("发送数据失败: %s" % (exc,), "io")
        hasher.update(data)
        sent += len(data)
        seq += 1
        frames += 1
        if rate is not None:
            rate.throttle(len(data))
        if progress is not None:
            try:
                progress(sent, size or sent)
            except Exception:
                pass
    try:
        sock.sendall(protocol.encode_chunk(seq, sent, b""))
    except OSError as exc:
        raise DataError("发送结束帧失败: %s" % (exc,), "io")
    elapsed = max(0.001, time.time() - started)
    return {"sent": sent, "frames": frames + 1, "seconds": elapsed,
            "digest": hasher.hexdigest()}


def recv_file(sock, fileobj, expected_size=0, progress=None, timeout=300.0,
              max_bytes=None, pause_event=None):
    """Receive chunk frames into *fileobj*.

    Returns ``{size, digest, frames}`` where ``digest`` is the SHA-KE-1024 of the
    received plaintext.  Offset/sequence gaps raise ``bad_order`` as required by
    PROTOCOL.md section 1.2.
    """
    framer = protocol.ChunkFramer()
    received = 0
    frames = 0
    seq_expected = 0
    hasher = filehash.Sha256()
    deadline = time.time() + float(timeout or 300.0)
    while True:
        if pause_event is not None and not pause_event.is_set():
            deadline += 0.5                # 暂停期间不累计超时
            pause_event.wait(0.5)
            continue
        remaining = deadline - time.time()
        if remaining <= 0:
            raise DataError("接收文件超时", "io")
        try:
            sock.settimeout(max(1.0, min(remaining, 60.0)))
        except OSError:
            pass
        frame = framer.pop()
        if frame is None:
            try:
                block = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as exc:
                raise DataError("接收数据失败: %s" % (exc,), "io")
            if not block:
                raise DataError("数据通道在结束帧之前断开", "io")
            framer.feed(block)
            continue
        seq, offset, payload = frame
        if not payload:
            if offset != received:
                raise DataError("结束帧偏移 %d != 已接收 %d" % (offset, received),
                                "bad_order")
            break
        if offset != received:
            raise DataError("数据块偏移不连续（%d != %d）" % (offset, received),
                            "bad_order")
        if seq != seq_expected:
            raise DataError("数据块序号跳变（%d != %d）" % (seq, seq_expected),
                            "bad_order")
        if max_bytes and received + len(payload) > max_bytes:
            raise DataError("接收数据超过声明大小", "too_large")
        try:
            fileobj.write(payload)
        except OSError as exc:
            raise DataError("写入文件失败: %s" % (exc,), "io")
        hasher.update(payload)
        received += len(payload)
        frames += 1
        seq_expected += 1
        if progress is not None:
            try:
                progress(received, int(expected_size or 0))
            except Exception:
                pass
    return {"size": received, "digest": hasher.hexdigest(), "frames": frames}


# ---------------------------------------------------------------------------
# Digest verification
# ---------------------------------------------------------------------------
def verify_hash(path, claimed_digest, delete_on_fail=True):
    """Recompute SHA-KE-1024 of *path* and compare with *claimed_digest*.

    Returns the actual 256 hex digest.  Raises :class:`DataError`
    (``err="checksum"``) and removes the file when the digests differ, exactly
    as PROTOCOL.md section 4.2 requires.  An empty *claimed_digest* is treated
    as "not provided" and only the actual digest is returned.
    """
    actual = filehash.sha256_file(path)
    claimed = str(claimed_digest or "").strip().lower()
    if claimed and claimed != actual:
        if delete_on_fail:
            try:
                os.remove(path)
            except OSError:
                pass
        raise DataError("SHA-256 校验失败（期望 %s…，实际 %s…）"
                        % (claimed[:16], actual[:16]), "checksum")
    return actual


# ---------------------------------------------------------------------------
# Transfer bookkeeping (shared by server and client)
# ---------------------------------------------------------------------------
class Transfer(object):
    """One announced transfer, in either direction.

    ``direction == "out"``: ``path`` is the source file; ``digest`` is filled by
    :func:`send_file` once the bytes went out.
    ``direction == "in"``: bytes are written to ``part``; on a verified ``hash``
    the caller moves it to ``path``.
    """

    __slots__ = ("tid", "token", "name", "direction", "size", "path", "part",
                 "digest", "received", "error", "event", "callback", "kind",
                 "link", "created", "pause_event", "paused")

    def __init__(self, tid, token, name, direction, path, size=0, kind="", link=None):
        self.tid = str(tid)
        self.token = str(token or "")
        self.name = str(name or "file.bin")
        self.direction = direction            # "in" | "out"
        self.size = int(size or 0)
        self.path = str(path)
        self.part = (self.path + ".part") if direction == "in" else ""
        self.digest = ""
        self.received = 0
        self.error = None
        self.event = threading.Event()
        # set = 正常运行；clear = 暂停（发送/接收循环在检查点等待）
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.paused = False
        self.callback = None
        self.kind = kind                      # "get" | "put"
        self.link = link                      # CTL session for "get" senders
        self.created = time.time()

    # -- completion --------------------------------------------------------
    def finish(self, digest=None, received=0, error=None):
        if digest:
            self.digest = str(digest).lower()
        if received:
            self.received = int(received)
        self.error = error
        self.event.set()

    def wait(self, timeout=300.0):
        return self.event.wait(timeout)

    @property
    def done(self):
        return self.event.is_set()

    def notify(self, received, total):
        """Report progress to the optional per-transfer callback."""
        self.received = int(received or 0)
        callback = self.callback
        if callback is None:
            return
        try:
            callback(int(received or 0), int(total or 0))
        except Exception:
            pass

    def to_dict(self):
        return {
            "id": self.tid, "name": self.name, "size": self.size,
            "received": self.received, "digest": self.digest,
            "error": self.error, "path": self.path, "part": self.part,
            "direction": self.direction, "kind": self.kind, "done": self.done,
        }


class TransferRegistry(object):
    """Thread safe ``id -> Transfer`` map."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = {}

    def register(self, tid, token, name, direction, path, size=0, kind="", link=None):
        transfer = Transfer(tid, token, name, direction, path, size, kind, link)
        with self._lock:
            self._items[transfer.tid] = transfer
        return transfer

    def get(self, tid):
        with self._lock:
            return self._items.get(str(tid))

    def pop(self, tid):
        with self._lock:
            return self._items.pop(str(tid), None)

    def pause(self, tid):
        """暂停一个传输（发送/接收循环会在检查点停下）。"""
        t = self.get(tid)
        if t is None:
            return False
        t.pause_event.clear()
        t.paused = True
        return True

    def resume(self, tid):
        """继续一个已暂停的传输。"""
        t = self.get(tid)
        if t is None:
            return False
        t.pause_event.set()
        t.paused = False
        return True

    def cancel(self, tid):
        transfer = self.pop(tid)
        if transfer is not None and not transfer.done:
            transfer.finish(error="cancelled")
        return transfer

    def items(self):
        with self._lock:
            return list(self._items.values())

    def prune(self, older_than=900.0):
        now = time.time()
        with self._lock:
            stale = [tid for tid, item in self._items.items()
                     if item.done and now - item.created > older_than]
            for tid in stale:
                del self._items[tid]
        return len(stale)

    def __len__(self):
        with self._lock:
            return len(self._items)
