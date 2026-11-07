# -*- coding: utf-8 -*-
"""TCP listener on port 47377: CTL sessions and the DATA channel.

PROTOCOL.md section 4.0: **the receiver opens the TCP connection**, the sender
listens and writes the frames.  Consequently, on this server an *accepted*
DATA connection is always one we must write on (we are the sender):

* ``get``  -- the peer asked us for a file; it connects, we stream + send hash
* ``put``  -- we pushed a file; the receiving peer connects, we stream

The reverse direction (we are the receiver) is an *outgoing* connection opened
by :meth:`_on_put_offer` for a push, or by :mod:`keshare.client` for ``get``.

One port serves both connection kinds: the first four bytes are ``b"DATA"`` for
the data channel, otherwise the four bytes are a CTL length prefix.
"""

import os
import socket
import struct
import threading
import time

from . import KSAPP_NAME, KSAPP_VERSION
from . import ctl, data, files, protocol
from .protocol import ProtocolError


class ServerError(Exception):
    """The listener could not be started (port busy, permissions, ...)."""


def _emit(callback, kind, payload):
    if callback is None:
        return
    try:
        callback(kind, payload)
    except Exception:
        pass


class KSServer(object):
    """Threaded server accepting CTL sessions and (sender-side) DATA links."""

    def __init__(self, config, log, registry=None, on_event=None, resolve_ip=None):
        self.config = config
        self.log = log
        self.registry = registry if registry is not None else data.TransferRegistry()
        self.on_event = on_event
        self.resolve_ip = resolve_ip
        self.share = files.ShareRoot(config.share_root)
        self.rate = protocol.RateLimiter(config.max_speed_kbps)
        self.port = int(config.port)
        self._sock = None
        self._thread = None
        self._bt = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._conns = set()
        self._active = 0
        self.bluetooth_active = False
        self.stats = {"ctl": 0, "rejected": 0, "sent": 0, "received": 0}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self):
        """Bind and start listening; returns the effective TCP port."""
        if self._thread is not None:
            return self.port
        bind_addr = self.config.bind_address or "0.0.0.0"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_addr, int(self.config.port)))
            sock.listen(16)
            sock.settimeout(0.5)
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            raise ServerError("无法监听 %s:%s -- %s" % (bind_addr, self.config.port, exc))
        self._sock = sock
        self.port = int(sock.getsockname()[1])
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="ks-accept")
        self._thread.daemon = True
        self._thread.start()
        self.log.info("%s %s 已启动: 数据通道 TCP %s:%d，共享目录 %s" % (
            KSAPP_NAME, KSAPP_VERSION, bind_addr, self.port, self.config.share_root))
        self._start_bluetooth()
        return self.port

    def _start_bluetooth(self):
        try:
            listener = ctl.BluetoothCtlListener(self.log, self._on_bluetooth_link)
            self.bluetooth_active = bool(listener.start())
            self._bt = listener
        except Exception as exc:                          # pragma: no cover - defensive
            self.log.warn("蓝牙监听初始化失败，控制通道使用 WiFi 回退: %s" % (exc,))
            self.bluetooth_active = False

    def stop(self):
        self._stop.set()
        if self._bt is not None:
            try:
                self._bt.stop()
            except Exception:
                pass
            self._bt = None
        self.bluetooth_active = False
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None:
            try:
                thread.join(1.5)
            except RuntimeError:
                pass
        with self._lock:
            conns = list(self._conns)
            self._conns = set()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        self.log.info("服务已停止")

    def rebind(self, port=None):
        if port is not None:
            self.config.data["network"]["port"] = int(port)
        self.stop()
        self.share = files.ShareRoot(self.config.share_root)
        self.rate.set_rate(self.config.max_speed_kbps)
        time.sleep(0.05)
        return self.start()

    def apply_settings(self):
        self.share = files.ShareRoot(self.config.share_root)
        self.rate.set_rate(self.config.max_speed_kbps)

    @property
    def transport_label(self):
        return "蓝牙" if self.bluetooth_active else "WiFi 回退"

    # ------------------------------------------------------------------
    # accept
    # ------------------------------------------------------------------
    def _accept_loop(self):
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                client, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                busy = self._active >= 8
                if not busy:
                    self._active += 1
            if busy:
                self.stats["rejected"] += 1
                self.log.warn("连接数已满（8），拒绝 %s" % (addr[0],))
                try:
                    client.close()
                except OSError:
                    pass
                continue
            thread = threading.Thread(target=self._connection_thread,
                                      args=(client, addr), name="ks-conn")
            thread.daemon = True
            thread.start()

    def _connection_thread(self, sock, addr):
        with self._lock:
            self._conns.add(sock)
        try:
            sock.settimeout(15.0)
            first = self._read_exactly(sock, 4)
            if first is None:
                return
            if first == protocol.DATA_PREFIX:
                self._handle_data_out(sock, addr, first)
            elif first == b"CTL ":
                # PROTOCOL.md 5.3: ``CTL <token>\n`` preamble followed by the
                # normal length-prefixed frames -- a full CTL session.
                self._read_line(sock)
                link = ctl.CtlLink(sock, transport="wifi", peer_ip=addr[0],
                                   log=self.log)
                self._run_ctl(link, addr[0])
            else:
                (length,) = struct.unpack(">I", first)
                if length > protocol.CTL_MAX_JSON:
                    self.log.warn("来自 %s 的连接首部非法（长度 %d），关闭"
                                  % (addr[0], length))
                    return
                link = ctl.CtlLink(sock, transport="wifi", peer_ip=addr[0],
                                   log=self.log, preloaded=first)
                self._run_ctl(link, addr[0])
        except (OSError, ProtocolError, ctl.CtlError, data.DataError) as exc:
            self.log.debug("连接 %s 结束: %s" % (addr[0], exc))
        except Exception as exc:                          # pragma: no cover - defensive
            self.log.error("连接 %s 处理异常: %s" % (addr[0], exc))
        finally:
            try:
                sock.close()
            except OSError:
                pass
            with self._lock:
                self._conns.discard(sock)
                self._active = max(0, self._active - 1)

    @staticmethod
    def _read_exactly(sock, count):
        chunks = []
        remaining = count
        while remaining > 0:
            block = sock.recv(remaining)
            if not block:
                return None
            chunks.append(block)
            remaining -= len(block)
        return b"".join(chunks)

    @staticmethod
    def _read_line(sock, max_bytes=128):
        """Consume a short ASCII line (used for the optional ``CTL <token>``)."""
        buffer = bytearray()
        while b"\n" not in buffer:
            if len(buffer) > max_bytes:
                return bytes(buffer)
            block = sock.recv(1)
            if not block:
                return bytes(buffer)
            buffer += block
        return bytes(buffer)

    def _on_bluetooth_link(self, sock, address):
        link = ctl.CtlLink(sock, transport="bt", peer_ip="", log=self.log)
        self._run_ctl(link, "bluetooth:%s" % (address,))
        try:
            sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def _transfer_event(self, state, **fields):
        payload = {"state": state}
        payload.update(fields)
        _emit(self.on_event, "transfer", payload)

    # ------------------------------------------------------------------
    # CTL session
    # ------------------------------------------------------------------
    def _run_ctl(self, link, peer_label):
        session = None
        try:
            session = ctl.handshake_server(link, self.config, self.port, timeout=20.0)
            self.stats["ctl"] += 1
            _emit(self.on_event, "ctl_open", session.summary())
            self._ctl_loop(session)
        except (ctl.CtlError, protocol.ProtocolError) as exc:
            self.log.warn("控制通道握手/会话失败（%s）: %s" % (peer_label, exc))
        except OSError as exc:
            self.log.debug("控制通道异常（%s）: %s" % (peer_label, exc))
        finally:
            if session is not None:
                _emit(self.on_event, "ctl_close", session.summary())
            try:
                link.close()
            except Exception:
                pass

    def _ctl_loop(self, session):
        while not self._stop.is_set():
            try:
                message = session.recv(timeout=10.0)
            except socket.timeout:
                continue
            except ctl.CtlError as exc:
                self.log.info("控制通道关闭: %s" % (exc,))
                return
            kind = message.get("t")
            try:
                if kind == protocol.M_BYE:
                    self.log.info("对端结束控制会话")
                    return
                if kind == protocol.M_LIST:
                    self._on_list(session, message)
                elif kind == protocol.M_STAT:
                    self._on_stat(session, message)
                elif kind == protocol.M_GET:
                    self._on_get(session, message)
                elif kind == protocol.M_PUT_OFFER:
                    self._on_put_offer(session, message)
                elif kind == protocol.M_HASH:
                    self._on_hash(session, message)
                elif kind == protocol.M_HASH_RESP:
                    ok = bool(message.get("ok"))
                    self.log.info("对端校验结果 %s: %s" % (
                        message.get("id"), "通过" if ok else message.get("err")))
                    self._transfer_event("done" if ok else "error",
                                         id=str(message.get("id") or ""),
                                         direction="out",
                                         err=None if ok else (message.get("err") or "checksum"))
                elif kind == protocol.M_ERR:
                    self.log.warn("对端错误: %s %s" % (
                        message.get("err"), message.get("msg") or ""))
                elif kind in (protocol.M_GET_RESP, protocol.M_PUT_RESP):
                    self.log.debug("收到非请求方应答 %r: %r" % (kind, message))
                else:
                    self.log.debug("忽略未知控制消息 %r" % (kind,))
                    self._safe_send(session, protocol.err_message(
                        "unsupported", "unknown message %r" % (kind,)))
            except protocol.ProtocolError as exc:
                self.log.warn("控制消息处理失败（%r）: %s" % (kind, exc))
            except (OSError, ctl.CtlError) as exc:
                self.log.info("控制通道中断: %s" % (exc,))
                return

    # ------------------------------------------------------------------
    # list / stat: browsing support for the remote file browser
    # ------------------------------------------------------------------
    def _token_ok(self, session, message):
        """The optional ``token`` field must match the session when present."""
        token = message.get("token")
        if not token or not session.token:
            return True
        return str(token) == session.token

    def _on_list(self, session, message):
        if not self._token_ok(session, message):
            self._safe_send(session, protocol.list_resp_message(
                [], False, "bad_token"))
            return
        path = str(message.get("path") or "")
        try:
            listing = self.share.list_dir(path, 500, 0)
        except files.SandboxError as exc:
            self._safe_send(session, protocol.list_resp_message([], False, "denied"))
            self.log.warn("拒绝越权目录 %r: %s" % (path, exc))
            return
        except FileNotFoundError:
            self._safe_send(session, protocol.list_resp_message([], False, "notfound"))
            return
        except OSError as exc:
            self._safe_send(session, protocol.list_resp_message([], False, "io"))
            self.log.warn("列出目录失败 %r: %s" % (path, exc))
            return
        self._safe_send(session, protocol.list_resp_message(listing["files"]))
        self.log.debug("list %r -> %d 项" % (path, len(listing["files"])))

    def _on_stat(self, session, message):
        if not self._token_ok(session, message):
            self._safe_send(session, protocol.stat_resp_message(
                "", ok=False, err="bad_token"))
            return
        path = str(message.get("path") or "")
        try:
            info = self.share.stat(path)
        except files.SandboxError as exc:
            self._safe_send(session, protocol.stat_resp_message(
                path, ok=False, err="denied"))
            self.log.warn("拒绝越权路径 %r: %s" % (path, exc))
            return
        except FileNotFoundError:
            self._safe_send(session, protocol.stat_resp_message(
                path, ok=False, err="notfound"))
            return
        except OSError as exc:
            self._safe_send(session, protocol.stat_resp_message(
                path, ok=False, err="io"))
            self.log.warn("stat 失败 %r: %s" % (path, exc))
            return
        self._safe_send(session, protocol.stat_resp_message(
            info.get("path", path), info.get("size", 0), info.get("mtime", 0),
            info.get("hash", "")))

    # ------------------------------------------------------------------
    # get: peer asks us for a file; the peer connects to our DATA port
    # ------------------------------------------------------------------
    def _on_get(self, session, message):
        try:
            protocol.require_fields(message, "id", "path")
            transfer_id = str(message["id"])[:64]
            path = str(message["path"])
            rel = files.normalise_rel(path, allow_empty=False)
            target = self.share.resolve(rel)
        except protocol.ProtocolError as exc:
            self._safe_send(session, protocol.get_resp_message(
                str(message.get("id") or ""), False, err=exc.err))
            return
        except files.SandboxError as exc:
            self._safe_send(session, protocol.get_resp_message(
                str(message.get("id") or ""), False, err="denied"))
            self.log.warn("拒绝越权路径 %r: %s" % (path, exc))
            return
        if not os.path.isfile(target):
            self._safe_send(session, protocol.get_resp_message(
                transfer_id, False, err="notfound"))
            return
        size = os.path.getsize(target)
        if size > self.config.max_file_bytes:
            self._safe_send(session, protocol.get_resp_message(
                transfer_id, False, err="too_large"))
            return
        name = files.basename_of(rel)
        self.registry.prune()
        self.registry.register(transfer_id, session.token, name, "out", target,
                               size, kind="get", link=session)
        self._safe_send(session, protocol.get_resp_message(
            transfer_id, True, size=size, name=name))
        self._transfer_event("start", id=transfer_id, name=name, size=size,
                             direction="out", peer=session.name)
        self.log.info("对端请求 %s（%d 字节），等待其数据通道连接" % (rel, size))

    # ------------------------------------------------------------------
    # put_offer: peer wants to push a file to us -> we connect and read
    # ------------------------------------------------------------------
    def _on_put_offer(self, session, message):
        if not self.config.allow_upload:
            self._safe_send(session, protocol.put_resp_message(
                str(message.get("id") or ""), False, "denied"))
            return
        try:
            protocol.require_fields(message, "id", "name", "size")
            transfer_id = str(message["id"])[:64]
            name = str(message["name"])
            size = int(message["size"])
        except (protocol.ProtocolError, TypeError, ValueError):
            self._safe_send(session, protocol.put_resp_message(
                str(message.get("id") or ""), False, "badreq"))
            return
        if size < 0 or size > self.config.max_file_bytes:
            self._safe_send(session, protocol.put_resp_message(
                transfer_id, False, "too_large"))
            return
        peer_ip = session.peer_ip or self._lookup_ip(session.did)
        if not peer_ip or not session.port:
            self._safe_send(session, protocol.put_resp_message(
                transfer_id, False, "no_wifi"))
            self.log.warn("无法确定 %s 的 WiFi 地址，拒绝推送" % (session.name,))
            return
        try:
            self.config.ensure_dirs()
            safe_name = files.safe_filename(name)
            dest = files.unique_path(self.config.receive_dir, safe_name)
        except (OSError, files.SandboxError) as exc:
            self.log.warn("准备接收目录失败: %s" % (exc,))
            self._safe_send(session, protocol.put_resp_message(
                transfer_id, False, "io"))
            return
        self.registry.prune()
        transfer = self.registry.register(transfer_id, session.token, safe_name,
                                          "in", dest, size, kind="put")
        self._safe_send(session, protocol.put_resp_message(transfer_id, True))
        self._transfer_event("start", id=transfer_id, name=safe_name, size=size,
                             direction="in", peer=session.name, dest=dest)
        thread = threading.Thread(target=self._receive_pushed_file,
                                  args=(transfer, peer_ip, session.port),
                                  name="ks-recv")
        thread.daemon = True
        thread.start()
        self.log.info("接受推送 %s（%d 字节），连接对端数据通道 %s:%s" % (
            safe_name, size, peer_ip, session.port))

    def _lookup_ip(self, did):
        if self.resolve_ip is None:
            return ""
        try:
            return self.resolve_ip(did) or ""
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # sender side: write a file on an accepted DATA connection
    # ------------------------------------------------------------------
    def _handle_data_out(self, sock, addr, first):
        self.log.debug("数据通道连接来自 %s（对端是接收方）" % (addr[0],))
        try:
            sock.settimeout(20.0)
            token, transfer_id = data.recv_data_header(sock, prefix=first)
        except data.DataError as exc:
            self.log.warn("数据通道首行无效: %s" % (exc,))
            return
        transfer = self.registry.get(transfer_id)
        if transfer is None or transfer.direction != "out":
            self.log.warn("数据通道引用了未知传输 %s" % (transfer_id,))
            return
        if transfer.token and token != transfer.token:
            self.log.warn("数据通道 token 不匹配，拒绝传输 %s" % (transfer_id,))
            return
        name = transfer.name
        last = [0]

        def progress(sent, total):
            transfer.received = int(sent or 0)      # 详情页/列表页要读这个字段
            if sent - last[0] >= 65536 or sent >= total:
                last[0] = sent
                self._transfer_event("progress", id=transfer_id, name=name,
                                     sent=sent, total=total or transfer.size,
                                     direction="out")

        try:
            with open(transfer.path, "rb") as handle:
                result = data.send_file(
                    sock, handle, transfer.size, chunk=self.config.chunk_bytes,
                    progress=progress, rate=self.rate,
                    pause_event=getattr(transfer, "pause_event", None),
                    should_cancel=lambda: getattr(transfer, "cancelled", False))
            transfer.finish(digest=result["digest"], received=result["sent"])
            self.stats["sent"] += 1
            self.log.info("已发送 %s -> %s:%s（%d 字节，%.0f KiB/s）" % (
                transfer.path, addr[0], addr[1], result["sent"],
                result["sent"] / 1024.0 / max(0.001, result["seconds"])))
            self._transfer_event("done", id=transfer_id, name=name,
                                 direction="out", size=result["sent"],
                                 digest=result["digest"])
            if transfer.kind == "get" and transfer.link is not None:
                try:
                    transfer.link.send(protocol.hash_message(
                        transfer_id, result["digest"], result["sent"]))
                except (ctl.CtlError, protocol.ProtocolError, OSError) as exc:
                    self.log.warn("发送 hash 失败: %s" % (exc,))
        except (data.DataError, OSError) as exc:
            err = getattr(exc, "err", "io")
            transfer.finish(error=err)
            self.log.warn("发送文件失败 %s: %s" % (transfer.path, exc))
            self._transfer_event("error", id=transfer_id, name=name,
                                 direction="out", err=err)

    def _receive_pushed_file(self, transfer, peer_ip, peer_port):
        """Receiver side of a push: connect to the sender and read frames."""
        sock = None
        last = [0]

        def progress(received, total):
            transfer.notify(received, total)
            if received - last[0] >= 65536 or (total and received >= total):
                last[0] = received
                self._transfer_event("progress", id=transfer.tid,
                                     name=transfer.name, sent=received,
                                     total=total or transfer.size,
                                     direction="in")

        part = transfer.part
        try:
            sock = data.open_data_socket(peer_ip, peer_port, timeout=20.0)
            data.send_data_header(sock, transfer.token, transfer.tid)
            folder = os.path.dirname(os.path.abspath(transfer.path))
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            with open(part, "wb") as handle:
                result = data.recv_file(sock, handle, expected_size=transfer.size,
                                        progress=progress, timeout=600.0,
                                        max_bytes=transfer.size or None,
                                        pause_event=getattr(transfer, "pause_event", None),
                                        should_cancel=lambda: getattr(transfer, "cancelled", False))
            if transfer.size and result["size"] != transfer.size:
                raise data.DataError("字节数与声明不一致（%d/%d）"
                                     % (result["size"], transfer.size), "checksum")
            transfer.finish(digest=result["digest"], received=result["size"])
            self.log.info("数据通道接收完成 %s（%d 字节），等待校验" % (
                transfer.name, result["size"]))
        except (data.DataError, OSError) as exc:
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
            err = getattr(exc, "err", "io")
            transfer.finish(error=err)
            self.log.warn("接收推送失败 %s: %s" % (transfer.name, exc))
            self._transfer_event("error", id=transfer.tid, name=transfer.name,
                                 direction="in", err=err)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # hash: sender declares the digest of what it sent
    # ------------------------------------------------------------------
    def _on_hash(self, session, message):
        try:
            protocol.require_fields(message, "id")
            transfer_id = str(message["id"])
        except protocol.ProtocolError as exc:
            self._safe_send(session, protocol.hash_resp_message(
                str(message.get("id") or ""), False, exc.err))
            return
        claimed = protocol.hash_digest(message)
        transfer = self.registry.get(transfer_id)
        if transfer is None or transfer.direction != "in":
            self._safe_send(session, protocol.hash_resp_message(
                transfer_id, False, "notfound"))
            self.log.warn("收到未知传输的校验消息: %s" % (transfer_id,))
            return
        if not transfer.wait(timeout=180.0):
            self.registry.cancel(transfer_id)
            try:
                os.remove(transfer.part)
            except OSError:
                pass
            self._safe_send(session, protocol.hash_resp_message(
                transfer_id, False, "io"))
            self._transfer_event("error", id=transfer_id, name=transfer.name,
                                 direction="in", err="timeout")
            return
        if transfer.error:
            self._safe_send(session, protocol.hash_resp_message(
                transfer_id, False, transfer.error))
            self._transfer_event("error", id=transfer_id, name=transfer.name,
                                 direction="in", err=transfer.error)
            return
        actual = transfer.digest
        if not claimed:
            claimed = actual
        if claimed != actual:
            try:
                os.remove(transfer.part)
            except OSError:
                pass
            self.registry.pop(transfer_id)
            self._safe_send(session, protocol.hash_resp_message(
                transfer_id, False, "checksum"))
            self._transfer_event("error", id=transfer_id, name=transfer.name,
                                 direction="in", err="checksum")
            self.log.warn("接收校验失败 %s（%s != %s）" % (
                transfer.name, claimed[:16], actual[:16]))
            return
        try:
            os.replace(transfer.part, transfer.path)
        except OSError as exc:
            self._safe_send(session, protocol.hash_resp_message(
                transfer_id, False, "io"))
            self._transfer_event("error", id=transfer_id, name=transfer.name,
                                 direction="in", err="io")
            self.log.warn("重命名接收文件失败 %s: %s" % (transfer.path, exc))
            return
        self.registry.pop(transfer_id)
        self._safe_send(session, protocol.hash_resp_message(transfer_id, True))
        self.stats["received"] += 1
        self._transfer_event("done", id=transfer_id, name=transfer.name,
                             direction="in", size=transfer.received,
                             digest=actual, dest=transfer.path)
        self.log.info("已接收并校验 %s（%d 字节）" % (transfer.name, transfer.received))

    # ------------------------------------------------------------------
    def _safe_send(self, session, message):
        try:
            session.send(message)
            return True
        except (ctl.CtlError, protocol.ProtocolError, OSError) as exc:
            self.log.warn("控制消息发送失败 %r: %s" % (message.get("t"), exc))
            return False
