# -*- coding: utf-8 -*-
"""CTL channel: JSON control messages over Bluetooth RFCOMM, or WiFi TCP.

Transport rules
---------------
* Primary: Bluetooth Classic RFCOMM (``socket.AF_BTH`` /
  ``socket.BTHPROTO_RFCOMM``).  CPython only exposes these constants when it was
  built with Bluetooth support and the host has a usable stack, so every call is
  wrapped in ``try/except``.  The listener binds the fixed RFCOMM channel
  :data:`BT_CHANNEL` (``3``) and the client connects to it; advertising a service
  record via SDP is not available from the Python standard library, so a peer
  must be told/guess the same channel.  This is documented as best effort.
* Fallback: the same length-prefixed JSON bytes over **WiFi TCP port 47377**.
  This is what the UI reports as ``控制通道: WiFi 回退``.

Framing (both transports): ``len:u32 BE | UTF-8 JSON``.

The handshake
-------------
A (initiator) sends ``hello``; B answers ``hello_ack`` carrying the session
``token = hex(SHA-256("KS1-CTL\\0" + did_A + nonce_A + did_B + nonce_B))[:16]``.
The token ties the DATA connection to this control session.
"""

import platform
import socket
import threading
import time

from . import crypto, protocol
from .protocol import ProtocolError

BT_CHANNEL = 3
BT_SERVICE_UUID = "5908C1A1-7F4E-4B2A-9C37-0B7C1B3B0001"


class CtlError(Exception):
    """CTL transport/handshake failure with a protocol error code."""

    def __init__(self, message, err="io"):
        Exception.__init__(self, message)
        self.err = err


def _os_version():
    try:
        return platform.version() or "windows"
    except Exception:                                     # pragma: no cover
        return "windows"


def bluetooth_available():
    """True when this Python build exposes AF_BTH RFCOMM sockets."""
    return (hasattr(socket, "AF_BTH")
            and hasattr(socket, "BTHPROTO_RFCOMM")
            and hasattr(socket, "BDADDR_ANY"))


# ---------------------------------------------------------------------------
# Link: framing over any stream socket
# ---------------------------------------------------------------------------
def _usable_host(value):
    """只接受 IPv4 私网/链路本地地址，避免被对端误导去连公网。"""
    try:
        import ipaddress
        ip = ipaddress.IPv4Address(str(value).strip())
    except Exception:
        return ""
    if ip.is_private or ip.is_link_local or str(ip).startswith("172.20.10."):
        return str(ip)
    return ""
class CtlLink(object):
    """Length-prefixed JSON over one connected stream socket."""

    def __init__(self, sock, transport="wifi", peer_ip="", log=None, preloaded=b""):
        self.sock = sock
        self.transport = transport            # "bt" | "wifi"
        self.peer_ip = peer_ip or ""
        self.log = log
        self._framer = protocol.CtlFramer()
        self._send_lock = threading.Lock()
        self.sent = 0
        self.received = 0
        if preloaded:
            self._framer.feed(preloaded)

    # -- metadata ----------------------------------------------------------
    @property
    def label(self):
        return "蓝牙" if self.transport == "bt" else "WiFi 回退"

    @property
    def data_label(self):
        return "WiFi"

    def settimeout(self, seconds):
        try:
            self.sock.settimeout(seconds)
        except OSError:
            pass

    def _log(self, level, *parts):
        """Log a CTL frame through the injected logger (object or callable)."""
        logger = self.log
        if logger is None:
            return
        if level == "DEBUG":
            protocol_fn = getattr(logger, "protocol", None)
            if callable(protocol_fn):
                try:
                    protocol_fn(*parts)
                    return
                except Exception:
                    return
        try:
            logger.log(level, *parts)
            return
        except AttributeError:
            pass
        except Exception:
            return
        try:
            logger("%s %s" % (level, " ".join(str(p) for p in parts)))
        except Exception:
            pass

    # -- io ----------------------------------------------------------------
    def send(self, msg):
        if not isinstance(msg, dict):
            raise CtlError("CTL 消息必须是对象")
        raw = protocol.encode_ctl(msg)
        with self._send_lock:
            try:
                self.sock.sendall(raw)
            except OSError as exc:
                raise CtlError("控制通道发送失败: %s" % (exc,), "io")
        self.sent += 1
        self._log("DEBUG", "ctl tx", msg.get("t"), "len=%d" % (len(raw) - 4,))
        return len(raw)

    def recv(self, timeout=None):
        """Return the next CTL message; raises socket.timeout when idle."""
        if timeout is not None:
            self.settimeout(timeout)
        while True:
            message = self._framer.pop()
            if message is not None:
                self.received += 1
                self._log("DEBUG", "ctl rx", message.get("t"), message.get("id", ""))
                return message
            try:
                block = self.sock.recv(65536)
            except socket.timeout:
                raise
            except OSError as exc:
                raise CtlError("控制通道读取失败: %s" % (exc,), "io")
            if not block:
                raise CtlError("控制通道已断开", "io")
            self._framer.feed(block)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class CtlSession(object):
    """One established CTL session plus the derived DATA token."""

    def __init__(self, link, initiator, peer_did, peer_name, peer_kind,
                 peer_osv, peer_port, peer_caps, own_nonce, peer_nonce, token):
        self.link = link
        self.initiator = bool(initiator)
        self.did = str(peer_did)
        self.name = str(peer_name or peer_did)
        self.kind = str(peer_kind or "other")
        self.osv = str(peer_osv or "")
        self.port = int(peer_port or 0)
        self.caps = int(peer_caps or 0)
        self.own_nonce = str(own_nonce)
        self.peer_nonce = str(peer_nonce)
        self.token = str(token)
        self.opened = time.time()

    # -- identity ----------------------------------------------------------
    @property
    def peer_ip(self):
        return self.link.peer_ip

    @property
    def transport_label(self):
        return self.link.label

    def send(self, msg):
        return self.link.send(msg)

    def recv(self, timeout=None):
        return self.link.recv(timeout)

    def close(self):
        try:
            self.send(protocol.bye_message())
        except (CtlError, ProtocolError):
            pass
        self.link.close()

    def summary(self):
        return {
            "did": self.did, "name": self.name, "kind": self.kind,
            "osv": self.osv, "port": self.port, "caps": self.caps,
            "token": self.token, "transport": self.link.transport,
            "transport_label": self.link.label, "peer_ip": self.peer_ip,
        }


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
def connect_wifi(host, port, timeout=10.0, log=None, token=None):
    """Open a CTL link over the WiFi TCP fallback.

    PROTOCOL.md section 5.3: the initiator first writes ``CTL <token>\\n``
    (``-`` when the session token does not exist yet, i.e. before
    ``hello_ack``), then the 4 byte length prefixed JSON frames.
    """
    try:
        sock = socket.create_connection((str(host), int(port)), float(timeout))
    except OSError as exc:
        raise CtlError("无法连接控制通道 %s:%s -- %s" % (host, port, exc), "io")
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    sock.settimeout(float(timeout))
    try:
        sock.sendall(protocol.ctl_preamble_line(token))
    except (OSError, protocol.ProtocolError) as exc:
        try:
            sock.close()
        except OSError:
            pass
        raise CtlError("发送 WiFi CTL 首行失败: %s" % (exc,), "io")
    return CtlLink(sock, transport="wifi", peer_ip=str(host), log=log)


def connect_bluetooth(address, channel=BT_CHANNEL, timeout=10.0, log=None):
    """Open a CTL link over Bluetooth RFCOMM (best effort)."""
    if not bluetooth_available():
        raise CtlError("此 Python 构建不支持蓝牙 RFCOMM", "unsupported")
    try:
        sock = socket.socket(socket.AF_BTH, socket.SOCK_STREAM,
                             socket.BTHPROTO_RFCOMM)
        sock.settimeout(float(timeout))
        sock.connect((str(address), int(channel)))
    except OSError as exc:
        raise CtlError("蓝牙控制通道连接失败: %s" % (exc,), "io")
    return CtlLink(sock, transport="bt", log=log)


class BluetoothCtlListener(object):
    """Accepts CTL sessions over Bluetooth RFCOMM (silently disabled if absent)."""

    def __init__(self, log, on_link, channel=BT_CHANNEL):
        self.log = log
        self.on_link = on_link
        self.channel = int(channel)
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self.available = False

    def start(self):
        if not bluetooth_available():
            self.log.info("蓝牙不可用（此 Python 构建没有 AF_BTH），控制通道使用 WiFi 回退")
            return False
        try:
            sock = socket.socket(socket.AF_BTH, socket.SOCK_STREAM,
                                 socket.BTHPROTO_RFCOMM)
        except (OSError, AttributeError) as exc:
            self.log.warn("蓝牙套接字创建失败，控制通道使用 WiFi 回退: %s" % (exc,))
            return False
        try:
            sock.bind((getattr(socket, "BDADDR_ANY", ""), self.channel))
            sock.listen(4)
            sock.settimeout(0.5)
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            self.log.warn("蓝牙监听失败（RFCOMM 通道 %d），控制通道使用 WiFi 回退: %s"
                          % (self.channel, exc))
            return False
        self._sock = sock
        self.available = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ks-bt-ctl")
        self._thread.daemon = True
        self._thread.start()
        self.log.info("蓝牙控制通道已监听（RFCOMM 通道 %d）" % (self.channel,))
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                client, address = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.on_link(client, address)
            except Exception as exc:                      # pragma: no cover
                self.log.warn("蓝牙连接处理失败: %s" % (exc,))
                try:
                    client.close()
                except OSError:
                    pass

    def stop(self):
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        thread = self._thread
        self._thread = None
        if thread is not None:
            try:
                thread.join(1.0)
            except RuntimeError:
                pass
        self.available = False


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------
def _recv_type(link, want, timeout):
    deadline = time.time() + float(timeout or 15.0)
    while True:
        left = deadline - time.time()
        if left <= 0:
            raise CtlError("等待 %s 超时" % (want,), "io")
        try:
            message = link.recv(max(0.5, min(left, 10.0)))
        except socket.timeout:
            continue
        except ProtocolError as exc:
            raise CtlError(str(exc), exc.err)
        kind = message.get("t")
        if kind == want:
            return message
        if kind == protocol.M_BYE:
            raise CtlError("对端结束了控制会话", "io")
        if link.log is not None:
            link.log.debug("控制通道忽略消息 %r" % (kind,))


def _adopt_host(link, message):
    """对端在 hello 里声明的 host 优先（蓝牙控制通道时本机拿不到对端 IP）。"""
    try:
        host = _usable_host(message.get("host") or "")
    except Exception:
        host = ""
    # 只有当前不知道对端地址时才采用（蓝牙控制通道等场景）
    if host and not (getattr(link, "peer_ip", "") or "").strip():
        link.peer_ip = host
def handshake_client(link, config, local_port, timeout=15.0):
    """Send ``hello`` and validate ``hello_ack`` (A side)."""
    nonce = crypto.rand_hex(8)                     # 16 hex chars
    hello = protocol.hello_message(
        config.device_id, config.device_name, "windows", _os_version(),
        int(local_port or config.port), protocol.CAPS_DEFAULT, nonce)
    try:
        link.send(hello)
        ack = _recv_type(link, protocol.M_HELLO_ACK, timeout)
        _adopt_host(link, ack)
        protocol.require_fields(ack, "did", "nonce", "token")
        token = crypto.token_for(config.device_id, nonce, ack["did"], ack["nonce"])
        if token != str(ack["token"]).lower():
            raise CtlError("控制通道 token 不一致（可能不是 KeShare 对端）", "auth_failed")
    except ProtocolError as exc:
        raise CtlError(str(exc), exc.err)
    session = CtlSession(
        link, True, ack["did"], ack.get("name"), ack.get("kind"),
        ack.get("osv"), ack.get("port"), ack.get("caps"),
        nonce, ack["nonce"], token)
    if link.log is not None:
        link.log.info("控制通道已建立（%s）: %s / %s" % (
            link.label, session.name, session.did))
    return session


def handshake_server(link, config, local_port, timeout=15.0):
    """Read ``hello`` and answer ``hello_ack`` (B side)."""
    nonce = crypto.rand_hex(8)
    try:
        hello = _recv_type(link, protocol.M_HELLO, timeout)
        _adopt_host(link, hello)
        protocol.require_fields(hello, "did", "nonce")
        token = crypto.token_for(hello["did"], hello["nonce"],
                                 config.device_id, nonce)
        ack = protocol.hello_ack_message(
            config.device_id, config.device_name, "windows", _os_version(),
            int(local_port or config.port), protocol.CAPS_DEFAULT, nonce, token)
        link.send(ack)
    except ProtocolError as exc:
        raise CtlError(str(exc), exc.err)
    session = CtlSession(
        link, False, hello["did"], hello.get("name"), hello.get("kind"),
        hello.get("osv"), hello.get("port"), hello.get("caps"),
        nonce, hello["nonce"], token)
    if link.log is not None:
        link.log.info("控制通道已建立（%s）: %s / %s" % (
            link.label, session.name, session.did))
    return session
