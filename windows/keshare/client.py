# -*- coding: utf-8 -*-
"""CTL client: hello handshake over Bluetooth / WiFi, then ``get`` and push.

PROTOCOL.md section 4.0: **the receiver opens the TCP connection**.

* ``get``: we are the receiver.  After ``get_resp`` we connect to the peer's
  DATA port, send ``DATA <token> <id>\\n`` and read the chunk frames; the peer
  then declares the digest over CTL (``hash``) and we answer ``hash_resp``.
* ``put``: the *peer* is the receiver, so it connects to **our** DATA port; our
  :class:`keshare.server.KSServer` writes the frames on that accepted socket.
  We only announce the transfer (``put_offer``), wait for the server-side
  writer to finish, then send ``hash`` and wait for ``hash_resp``.

Because of that the client needs ``local_port`` (the port our own server listens
on) and the shared :class:`keshare.data.TransferRegistry`.
"""

import os
import socket
import time

from . import ctl, crypto, data, files, protocol


class KSClientError(Exception):
    """High level client failure with a protocol error code."""

    def __init__(self, message, err="io"):
        Exception.__init__(self, message)
        self.err = err


def _emit(callback, kind, payload):
    if callback is None:
        return
    try:
        callback(kind, payload)
    except Exception:
        pass


class KSClient(object):
    """One CTL session to one peer."""

    def __init__(self, config, host, port=None, log=None, local_port=None,
                 registry=None, on_event=None, resolve_ip=None, timeout=20.0):
        self.config = config
        self.host = str(host)
        self.port = int(port or config.port)
        self.log = log
        self.local_port = int(local_port or config.port)
        self.registry = registry if registry is not None else data.TransferRegistry()
        self.on_event = on_event
        self.resolve_ip = resolve_ip
        self.timeout = float(timeout)
        self.rate = protocol.RateLimiter(config.max_speed_kbps)
        self.link = None
        self.session = None
        self._closed = False

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    def connect(self, timeout=None):
        """Open the CTL link and run the hello handshake."""
        if self.session is not None:
            return self.session
        timeout = float(timeout or self.timeout)
        link = ctl.connect_wifi(self.host, self.port, timeout, log=self.log)
        try:
            session = ctl.handshake_client(link, self.config, self.local_port,
                                           timeout=timeout)
        except Exception:
            try:
                link.close()
            except Exception:
                pass
            raise
        self.link = link
        self.session = session
        if self.log is not None:
            self.log.info("已连接 %s:%d（控制通道 %s）" % (
                self.host, self.port, link.label))
        return session

    def connect_usb(self, device_id=None, timeout=None):
        """USB 传输：经 usbmuxd 隧道连到 iOS 的 47377 监听端口。"""
        timeout = float(timeout or 15.0)
        link = ctl.connect_usb(self.port, device_id=device_id, timeout=timeout, log=self.log)
        session = ctl.CtlSession(link, self.config, self.local_port, log=self.log,
                                 registry=self.registry, on_event=self.on_event,
                                 resolve_ip=self.resolve_ip)
        reply = session.handshake_client(timeout=timeout)
        self._session = session
        self.peer = reply
        self.peer_ip = "usb"
        return reply
    def connect_bluetooth(self, address, channel=ctl.BT_CHANNEL, timeout=None):
        """Try the Bluetooth CTL transport instead of the WiFi fallback."""
        timeout = float(timeout or self.timeout)
        link = ctl.connect_bluetooth(address, channel, timeout, log=self.log)
        try:
            session = ctl.handshake_client(link, self.config, self.local_port,
                                           timeout=timeout)
        except Exception:
            try:
                link.close()
            except Exception:
                pass
            raise
        self.link = link
        self.session = session
        return session

    def close(self, notify=True):
        if self._closed:
            return
        self._closed = True
        session = self.session
        self.session = None
        self.link = None
        if session is None:
            return
        try:
            if notify:
                session.send(protocol.bye_message())
        except Exception:
            pass
        try:
            session.link.close()
        except Exception:
            pass
        if self.log is not None:
            self.log.info("控制通道已关闭 %s" % (self.host,))

    @property
    def connected(self):
        return self.session is not None

    @property
    def peer_name(self):
        return self.session.name if self.session else ""

    @property
    def transport_label(self):
        return self.link.label if self.link is not None else "WiFi 回退"

    def _require(self):
        if self.session is None or self.link is None:
            raise KSClientError("尚未连接", "badreq")
        return self.session

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _transfer_event(self, state, **fields):
        payload = {"state": state}
        payload.update(fields)
        _emit(self.on_event, "transfer", payload)

    def _peer_ip(self):
        session = self._require()
        ip = session.peer_ip
        if not ip and self.resolve_ip is not None:
            try:
                ip = self.resolve_ip(session.did) or ""
            except Exception:
                ip = ""
        return ip

    def _recv_until(self, wanted, deadline):
        """Receive CTL messages until one of *wanted* arrives."""
        session = self._require()
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise KSClientError("等待 %s 超时" % ("/".join(wanted),), "io")
            try:
                message = session.recv(max(0.5, min(remaining, 10.0)))
            except socket.timeout:
                continue
            except (ctl.CtlError, protocol.ProtocolError) as exc:
                raise KSClientError(str(exc), getattr(exc, "err", "io"))
            kind = message.get("t")
            if kind in wanted:
                return message
            if kind == protocol.M_BYE:
                raise KSClientError("对端结束了控制会话", "io")
            if kind == protocol.M_ERR:
                raise KSClientError(message.get("msg") or message.get("err") or "对端错误",
                                    message.get("err") or "io")
            if self.log is not None:
                self.log.debug("控制通道忽略消息 %r" % (kind,))

    # ------------------------------------------------------------------
    # browsing: list / stat (PROTOCOL.md section 3)
    # ------------------------------------------------------------------
    def list_files(self, path="", timeout=60.0):
        """Return the peer's ``list_resp`` header (``files`` list inside)."""
        session = self._require()
        session.send(protocol.list_message(session.token, path))
        deadline = time.time() + float(timeout)
        response = self._recv_until((protocol.M_LIST_RESP,), deadline)
        if not response.get("ok"):
            raise KSClientError(response.get("err") or "列表请求被拒绝",
                                response.get("err") or "io")
        return response

    def stat(self, path, timeout=60.0):
        """Return the peer's ``stat_resp`` header (``size``/``mtime``/``hash``)."""
        session = self._require()
        session.send(protocol.stat_message(session.token, path))
        deadline = time.time() + float(timeout)
        response = self._recv_until((protocol.M_STAT_RESP,), deadline)
        if not response.get("ok"):
            raise KSClientError(response.get("err") or "stat 请求被拒绝",
                                response.get("err") or "io")
        return response

    # ------------------------------------------------------------------
    # download (get): we are the receiver, so we connect to the peer
    # ------------------------------------------------------------------
    def get(self, remote_path, dest_path=None, progress=None, timeout=900.0):
        """Ask the peer for *remote_path*, connect to its DATA port and read it."""
        session = self._require()
        try:
            rel = files.normalise_rel(remote_path, allow_empty=False)
        except files.SandboxError as exc:
            raise KSClientError("远端路径无效: %s" % (exc,), "denied")
        name = files.basename_of(rel) or "download.bin"
        if dest_path is None:
            self.config.ensure_dirs()
            dest_path = files.unique_path(self.config.receive_dir, name)
        part = dest_path + ".part"
        transfer_id = crypto.rand_hex(4)               # 8 hex chars
        started = time.time()
        deadline = started + float(timeout or 900.0)
        self._transfer_event("start", id=transfer_id, name=name, size=0,
                             direction="in", peer=session.name, dest=dest_path)
        sock = None
        try:
            session.send(protocol.get_message(session.token, transfer_id, rel))
            response = self._recv_until((protocol.M_GET_RESP,), deadline)
            if not response.get("ok"):
                raise KSClientError(response.get("err") or "请求被拒绝",
                                    response.get("err") or "denied")
            size = int(response.get("size") or 0)
            remote_name = response.get("name") or name
            self._transfer_event("start", id=transfer_id, name=remote_name,
                                 size=size, direction="in", peer=session.name,
                                 dest=dest_path)
            peer_ip = self._peer_ip()
            peer_port = int(session.port or self.port)
            sock = data.open_data_socket(peer_ip, peer_port, timeout=20.0)
            data.send_data_header(sock, session.token, transfer_id)
            folder = os.path.dirname(os.path.abspath(dest_path))
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            with open(part, "wb") as handle:
                result = data.recv_file(sock, handle, expected_size=size,
                                        progress=progress,
                                        timeout=max(60.0, deadline - time.time()),
                                        max_bytes=size or None)
            try:
                sock.close()
            except OSError:
                pass
            sock = None

            message = self._recv_until((protocol.M_HASH,), deadline)
            claimed = protocol.hash_digest(message)
            actual = result["digest"]
            if (claimed and claimed != actual) or (size and result["size"] != size):
                try:
                    os.remove(part)
                except OSError:
                    pass
                session.send(protocol.hash_resp_message(transfer_id, False, "checksum"))
                raise KSClientError("SHA-256 校验失败", "checksum")
            os.replace(part, dest_path)
            session.send(protocol.hash_resp_message(transfer_id, True))
            elapsed = max(0.001, time.time() - started)
            self._transfer_event("done", id=transfer_id, name=remote_name,
                                 direction="in", size=result["size"],
                                 digest=actual, dest=dest_path)
            if self.log is not None:
                self.log.info("已下载 %s -> %s（%d 字节）" % (
                    rel, dest_path, result["size"]))
            return {
                "id": transfer_id, "path": rel, "dest": dest_path,
                "name": remote_name, "size": result["size"],
                "hash": actual, "sha_ke_1024": actual, "seconds": elapsed,
            }
        except Exception as exc:
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
            err = getattr(exc, "err", "io")
            self._transfer_event("error", id=transfer_id, name=name,
                                 direction="in", err=err)
            if self.log is not None:
                self.log.warn("下载 %s 失败: %s" % (rel, exc))
            raise
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # upload (put): the peer is the receiver and connects back to us
    # ------------------------------------------------------------------
    def push(self, local_path, remote_name=None, progress=None, timeout=900.0):
        """Push a local file to the peer (our server writes, peer connects)."""
        session = self._require()
        local_path = os.path.abspath(local_path)
        if not os.path.isfile(local_path):
            raise KSClientError("文件不存在: %s" % (local_path,), "notfound")
        size = os.path.getsize(local_path)
        if size > self.config.max_file_bytes:
            raise KSClientError("文件超过大小限制", "too_large")
        name = files.safe_filename(remote_name or os.path.basename(local_path))
        transfer_id = crypto.rand_hex(4)
        started = time.time()
        deadline = started + float(timeout or 900.0)
        transfer = self.registry.register(transfer_id, session.token, name, "out",
                                          local_path, size, kind="put")
        transfer.callback = progress
        self._transfer_event("start", id=transfer_id, name=name, size=size,
                             direction="out", peer=session.name)
        try:
            session.send(protocol.put_offer_message(transfer_id, name, size))
            response = self._recv_until((protocol.M_PUT_RESP,), deadline)
            if not response.get("ok"):
                raise KSClientError(response.get("err") or "对端拒绝接收",
                                    response.get("err") or "denied")
            if not transfer.wait(timeout=max(30.0, deadline - time.time())):
                raise KSClientError("等待对端数据通道超时", "io")
            if transfer.error:
                raise KSClientError("发送失败: %s" % (transfer.error,),
                                    transfer.error)
            digest = transfer.digest
            session.send(protocol.hash_message(transfer_id, digest, size))
            verdict = self._recv_until((protocol.M_HASH_RESP,), deadline)
            if not verdict.get("ok"):
                err = verdict.get("err") or "checksum"
                raise KSClientError("对端校验失败: %s" % (err,), err)
            elapsed = max(0.001, time.time() - started)
            self._transfer_event("done", id=transfer_id, name=name,
                                 direction="out", size=size, digest=digest,
                                 peer=session.name)
            if self.log is not None:
                self.log.info("已推送 %s -> %s（%d 字节）" % (
                    name, session.name, size))
            return {
                "id": transfer_id, "name": name, "size": size,
                "hash": digest, "sha_ke_1024": digest,
                "seconds": elapsed, "dest": name,
            }
        except Exception as exc:
            err = getattr(exc, "err", "io")
            self._transfer_event("error", id=transfer_id, name=name,
                                 direction="out", err=err)
            if self.log is not None:
                self.log.warn("推送 %s 失败: %s" % (name, exc))
            raise
        finally:
            self.registry.pop(transfer_id)
