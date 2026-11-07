# -*- coding: utf-8 -*-
"""Apple 驱动检测与安装引导。

KeShare 的 USB 传输依赖 iTunes 提供的 **Apple Mobile Device Service**（usbmuxd）。
没装时给出明确提示，并尽量走官方渠道安装（不随包分发 Apple 的二进制，
避免许可问题）：

  方式一：winget 静默安装 iTunes        ``winget install --id Apple.iTunes``
  方式二：Microsoft Store 的「Apple 设备」  ``ms-windows-store://pdp/?productid=9NP83LWLPZ9K``
  方式三：打开 Apple 官方下载页           https://support.apple.com/downloads
"""
from __future__ import annotations

import os
import socket
import subprocess
import winreg

SERVICE_NAME = "Apple Mobile Device Service"
USBMUX_PORT = 27015
STORE_APPLE_DEVICES = "9NP83LWLPZ9K"
DOWNLOAD_PAGE = "https://support.apple.com/downloads"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd, timeout=25, **kw):
    try:
        return subprocess.run(cmd, timeout=timeout, check=False,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=_NO_WINDOW, **kw)
    except Exception:
        return None


def port_reachable(timeout=1.5):
    """usbmuxd 是否在监听（最直接的可用性判据）。"""
    try:
        sock = socket.create_connection(("127.0.0.1", USBMUX_PORT), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


def service_state():
    """返回 'running' / 'stopped' / ''（未安装）。"""
    result = _run(["sc", "query", SERVICE_NAME])
    if not result or result.returncode != 0:
        return ""
    text = (result.stdout or b"").decode("mbcs", "replace")
    if "RUNNING" in text:
        return "running"
    if "STOPPED" in text:
        return "stopped"
    return "present"


def installed_products():
    """从注册表读出已安装的 Apple 组件名称。"""
    names = []
    keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for root in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as base:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(base, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(base, sub) as item:
                            try:
                                name = winreg.QueryValueEx(item, "DisplayName")[0]
                            except OSError:
                                continue
                            if any(k in name for k in ("iTunes", "Apple Mobile", "Apple Devices", "Apple 设备")):
                                names.append(name)
                    except OSError:
                        continue
        except OSError:
            continue
    return names


def status():
    """综合判断 USB 传输是否可用。"""
    state = service_state()
    port = port_reachable()
    products = installed_products()
    present = bool(state) or port or bool(products)
    return {
        "present": present,
        "service": state,
        "port": port,
        "products": products,
        "ready": port,
    }


def has_winget():
    result = _run(["where", "winget"], timeout=8)
    return bool(result and result.returncode == 0)


def install_itunes_via_winget(on_output=None):
    """用 winget 静默安装 iTunes（含 Apple Mobile Device Service）。"""
    if not has_winget():
        return {"ok": False, "err": "系统没有 winget，请用商店或官网安装"}
    cmd = ["winget", "install", "--id", "Apple.iTunes", "--source", "winget",
           "--accept-package-agreements", "--accept-source-agreements",
           "--disable-interactivity", "--silent"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=_NO_WINDOW)
    except Exception as exc:
        return {"ok": False, "err": str(exc)}
    output = []
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            text = line.decode("mbcs", "replace").strip()
            if text:
                output.append(text)
                if on_output:
                    on_output(text)
        proc.wait(timeout=900)
    except Exception as exc:
        return {"ok": False, "err": str(exc), "log": output}
    ok = proc.returncode == 0
    return {"ok": ok, "err": "" if ok else "winget 返回 %d" % proc.returncode, "log": output}


def open_store_page():
    """打开 Microsoft Store 的「Apple 设备」页面。"""
    try:
        os.startfile("ms-windows-store://pdp/?productid=%s" % STORE_APPLE_DEVICES)
        return True
    except Exception:
        return open_download_page()


def open_download_page():
    try:
        os.startfile(DOWNLOAD_PAGE)
        return True
    except Exception:
        return False


def start_service():
    """尝试启动服务（已安装但停止时）。"""
    result = _run(["net", "start", SERVICE_NAME], timeout=40)
    return bool(result and result.returncode == 0)


if __name__ == "__main__":
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    print("winget:", has_winget())
