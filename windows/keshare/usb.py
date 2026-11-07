# -*- coding: utf-8 -*-
"""USB 传输：通过 iTunes 的 usbmuxd（Apple Mobile Device Service）转发到设备端口。

原理
----
`Apple Mobile Device Service` 在 127.0.0.1:27015 提供 usbmux 服务。向它发一个
`Connect` 请求（plist）即可把一条 TCP 连接**穿过 USB 线**转发到 iPhone 上监听的端口。
KeShare iOS 端本来就在 47377 上监听，因此：

    控制通道 = usbmux 隧道 → iOS:47377（裸长度帧）
    数据通道 = 另一条隧道  → iOS:47377（首行 "DATA <token> <id>\\n"）

和 WiFi 完全同一套协议，**不需要越狱、不需要改 App**。

前置条件
--------
* Windows 装了 iTunes 或「Apple 设备」（提供 Apple Mobile Device Service）
* 数据线连上并在 iPhone 上点「信任本电脑」

模块不依赖任何第三方包，plist 用最小 XML 实现。
"""
from __future__ import annotations

import socket
import struct
import threading

USBMUX_HOST = "127.0.0.1"
USBMUX_PORT = 27015

_HDR = struct.Struct("<IIII")   # length, version, message, tag
_MSG_PLIST = 8                  # MESSAGE_PLIST


class UsbError(Exception):
    """USB 通道不可用（没装 iTunes / 没连设备 / 未信任）。"""


# ----------------------------------------------------------------- plist
def _xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _plist_dict(d):
    """把 Python dict 输出成 Apple 的 XML plist。"""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
           '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
           '<plist version="1.0"><dict>']
    for key, value in d.items():
        out.append("<key>%s</key>" % _xml_escape(key))
        if isinstance(value, bool):
            out.append("<true/>" if value else "<false/>")
        elif isinstance(value, int):
            out.append("<integer>%d</integer>" % value)
        elif isinstance(value, (dict, list)):
            raise UsbError("plist 暂不支持嵌套结构")
        else:
            out.append("<string>%s</string>" % _xml_escape(value))
    out.append("</dict></plist>")
    return "".join(out).encode("utf-8")


def _plist_parse(data):
    """极简 XML plist 解析：返回 dict / list / 标量。"""
    text = data.decode("utf-8", "replace")
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        lt = text.find("<", i)
        if lt < 0:
            break
        gt = text.find(">", lt)
        if gt < 0:
            break
        tag = text[lt + 1:gt].strip()
        i = gt + 1
        if tag.startswith("?") or tag.startswith("!"):
            continue
        if tag.startswith("plist"):
            continue
        if tag.startswith("dict") or tag.startswith("array"):
            tokens.append(("open", tag.split()[0]))
            continue
        if tag.startswith("/"):
            tokens.append(("close", tag[1:].strip()))
            continue
        if tag in ("true", "false"):
            tokens.append(("value", tag == "true"))
            continue
        if tag in ("integer", "real", "string", "data", "date"):
            close = text.find("</%s>" % tag, i)
            raw = text[i:close] if close >= 0 else ""
            i = (close + len(tag) + 3) if close >= 0 else n
            if tag == "integer":
                try:
                    tokens.append(("value", int(raw.strip() or "0")))
                except ValueError:
                    tokens.append(("value", 0))
            else:
                tokens.append(("value", raw))
            continue
        if tag == "key":
            close = text.find("</key>", i)
            raw = text[i:close] if close >= 0 else ""
            i = (close + 6) if close >= 0 else n
            tokens.append(("key", raw))
            continue
    # 组装
    pos = [0]

    def parse_value():
        if pos[0] >= len(tokens):
            return None
        kind, val = tokens[pos[0]]
        pos[0] += 1
        if kind == "value":
            return val
        if kind == "open" and val == "dict":
            result = {}
            while pos[0] < len(tokens):
                k, v = tokens[pos[0]]
                if k == "close":
                    pos[0] += 1
                    break
                if k != "key":
                    pos[0] += 1
                    continue
                pos[0] += 1
                result[v] = parse_value()
            return result
        if kind == "open" and val == "array":
            result = []
            while pos[0] < len(tokens):
                k, v = tokens[pos[0]]
                if k == "close":
                    pos[0] += 1
                    break
                result.append(parse_value())
            return result
        return None

    # 跳到第一个 dict/array
    while pos[0] < len(tokens) and tokens[pos[0]][0] not in ("open",):
        pos[0] += 1
    return parse_value()


# ----------------------------------------------------------------- 传输
def _send_plist(sock, message, tag=1):
    payload = _plist_dict(message)
    sock.sendall(_HDR.pack(16 + len(payload), 1, _MSG_PLIST, tag) + payload)


def _recv_exact(sock, count):
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise UsbError("usbmux 连接中断")
        buf += chunk
    return buf


def _recv_plist(sock):
    head = _recv_exact(sock, 16)
    length, _version, _message, _tag = _HDR.unpack(head)
    if length < 17 or length > 4 * 1024 * 1024:
        raise UsbError("usbmux 返回长度异常")
    return _plist_parse(_recv_exact(sock, length - 16))


def _open_usbmux(timeout=5.0):
    try:
        sock = socket.create_connection((USBMUX_HOST, USBMUX_PORT), timeout=timeout)
    except OSError as exc:
        raise UsbError("连不上 usbmuxd（127.0.0.1:27015）：请安装 iTunes 或「Apple 设备」并连接数据线") from exc
    return sock


def list_devices(timeout=5.0):
    """列出 USB 上的 iOS 设备，返回 [{device_id, serial, connection}]。"""
    sock = _open_usbmux(timeout)
    try:
        _send_plist(sock, {"MessageType": "ListDevices",
                           "ClientVersionString": "KeShare-1.0",
                           "ProgName": "KeShare"}, tag=2)
        reply = _recv_plist(sock)
    finally:
        sock.close()
    devices = []
    items = reply.get("DeviceList") if isinstance(reply, dict) else None
    if not isinstance(items, list):
        return devices
    for item in items:
        props = item.get("Properties") if isinstance(item, dict) else None
        if not isinstance(props, dict):
            continue
        devices.append({
            "device_id": item.get("DeviceID") or props.get("DeviceID") or 0,
            "serial": props.get("SerialNumber") or "",
            "name": props.get("DeviceName") or props.get("SerialNumber") or "",
            "connection": props.get("ConnectionType") or "USB",
        })
    return devices


def open_tunnel(port=47377, device_id=None, timeout=6.0):
    """建立一条穿过 USB 的 TCP 隧道到设备端口，返回已连接的 socket。"""
    devices = list_devices(timeout)
    usb_devices = [d for d in devices if str(d.get("connection", "USB")).upper().startswith("USB")]
    if device_id is not None:
        usb_devices = [d for d in usb_devices if d.get("device_id") == device_id] or usb_devices
    if not usb_devices:
        raise UsbError("没有通过 USB 连接的 iOS 设备（请插好数据线并在手机上点“信任”）")

    last_error = None
    for dev in usb_devices:
        sock = _open_usbmux(timeout)
        try:
            # usbmuxd 要求端口按网络字节序放在整型里
            net_port = struct.unpack(">H", struct.pack("<H", int(port)))[0]
            _send_plist(sock, {
                "MessageType": "Connect",
                "ClientVersionString": "KeShare-1.0",
                "ProgName": "KeShare",
                "DeviceID": int(dev.get("device_id") or 0),
                "PortNumber": net_port,
            }, tag=3)
            reply = _recv_plist(sock)
            number = reply.get("Number") if isinstance(reply, dict) else None
            if number == 0:
                sock.settimeout(30.0)
                return sock
            last_error = "usbmux 拒绝连接端口 %d（Number=%r）" % (port, number)
        except UsbError as exc:
            last_error = str(exc)
        except OSError as exc:
            last_error = "usbmux 通信失败：%s" % (exc,)
        sock.close()
    raise UsbError(last_error or "USB 隧道建立失败")


def available():
    """是否具备 USB 传输条件。"""
    try:
        _open_usbmux(2.0).close()
    except UsbError:
        return False
    return True


# ----------------------------------------------------------------- 自测
if __name__ == "__main__":
    print("usbmuxd 可用:", available())
    try:
        print("设备:", list_devices())
    except UsbError as exc:
        print("设备: 无 -", exc)
