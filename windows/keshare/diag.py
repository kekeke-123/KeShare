# -*- coding: utf-8 -*-
"""Runtime diagnostics for the developer tab (stdlib + ctypes only).

``process_info()`` gathers everything the "导出调试参数" button writes: pid,
Python version, platform, cwd, config/log paths, local IPv4 addresses, open
socket count and the live thread list.

Open sockets are counted through ``iphlpapi.GetExtendedTcpTable`` /
``GetExtendedUdpTable`` filtered by the current pid.  That API is
Vista-compatible; if it is unavailable the count is reported as ``None``.
"""

import ctypes
import os
import platform
import socket
import sys
import threading

AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
UDP_TABLE_OWNER_PID = 1


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", ctypes.c_ulong),
        ("dwLocalAddr", ctypes.c_ulong),
        ("dwLocalPort", ctypes.c_ulong),
        ("dwRemoteAddr", ctypes.c_ulong),
        ("dwRemotePort", ctypes.c_ulong),
        ("dwOwningPid", ctypes.c_ulong),
    ]


class _MIB_UDPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwLocalAddr", ctypes.c_ulong),
        ("dwLocalPort", ctypes.c_ulong),
        ("dwOwningPid", ctypes.c_ulong),
    ]


def ip_addresses():
    """Local IPv4 addresses (best effort, never raises)."""
    found = []
    try:
        host, _aliases, addresses = socket.gethostbyname_ex(socket.gethostname())
        for address in addresses:
            if address not in found:
                found.append(address)
    except OSError:
        pass
    if not found:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("192.0.2.1", 9))       # no packet is sent
                found.append(probe.getsockname()[0])
            finally:
                probe.close()
        except OSError:
            pass
    return found or ["127.0.0.1"]


def count_open_sockets():
    """Number of TCP/UDP sockets owned by this process (``None`` if unknown)."""
    if not sys.platform.startswith("win"):
        try:
            return len(os.listdir("/proc/self/fd"))
        except OSError:
            return None
    try:
        iphlpapi = ctypes.windll.iphlpapi
    except (AttributeError, OSError):
        return None
    return _count_windows_sockets(iphlpapi, os.getpid())


def _count_windows_sockets(iphlpapi, pid):
    total = 0
    for function, table_class, row_type in (
            (iphlpapi.GetExtendedTcpTable, TCP_TABLE_OWNER_PID_ALL,
             _MIB_TCPROW_OWNER_PID),
            (iphlpapi.GetExtendedUdpTable, UDP_TABLE_OWNER_PID,
             _MIB_UDPROW_OWNER_PID)):
        try:
            size = ctypes.c_ulong(0)
            function(None, ctypes.byref(size), False, AF_INET, table_class, 0)
            if size.value == 0:
                continue
            buffer = ctypes.create_string_buffer(size.value)
            result = function(buffer, ctypes.byref(size), False, AF_INET,
                              table_class, 0)
            if result != 0:
                continue
            count = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ulong)).contents.value
            if not count:
                continue
            offset = ctypes.sizeof(ctypes.c_ulong)
            rows = ctypes.cast(ctypes.addressof(buffer) + offset,
                               ctypes.POINTER(row_type))
            for index in range(int(count)):
                if int(rows[index].dwOwningPid) == pid:
                    total += 1
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return total


def thread_list():
    """Snapshot of live threads as ``{name, alive, daemon}`` dicts."""
    out = []
    for thread in threading.enumerate():
        out.append({
            "name": thread.getName() if hasattr(thread, "getName") else thread.name,
            "alive": thread.is_alive(),
            "daemon": thread.daemon,
        })
    return out


def process_info(config=None, logger=None, server=None, extra=None):
    """Collect the debug-parameter dictionary exported by the developer tab."""
    info = {
        "app": "KeShare",
        "pid": os.getpid(),
        "python": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "python_3_8_plus": sys.version_info >= (3, 8),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cwd": os.getcwd(),
        "config_path": getattr(config, "path", ""),
        "integrity": "SHA-KE-1024",
        "digest_hex_chars": 256,
        "share_root": getattr(config, "share_root", ""),
        "receive_dir": getattr(config, "receive_dir", ""),
        "server_port": getattr(server, "port", None),
        "server_bluetooth": bool(getattr(server, "bluetooth_active", False)),
        "log_path": getattr(logger, "path", ""),
        "ip_addresses": ip_addresses(),
        "open_sockets": count_open_sockets(),
        "threads": thread_list(),
        "thread_count": threading.active_count(),
    }
    if extra:
        info.update(extra)
    return info
