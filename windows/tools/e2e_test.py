# -*- coding: utf-8 -*-
"""端到端联调：假 iOS 端 + Windows 客户端，跑通「发现 → 握手 → 列目录 → 下载 → 校验」。

用法（在 D:\\KeShare\\windows 下）：
    python tools/e2e_test.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keshare import config as config_mod, protocol, ke1024, log as log_mod  # noqa: E402
from keshare.client import KSClient  # noqa: E402
from keshare.server import KSServer  # noqa: E402
from tools import fake_ios  # noqa: E402


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def main():
    work = tempfile.mkdtemp(prefix="ks-e2e-")
    share = os.path.join(work, "ios-share")
    os.makedirs(share, exist_ok=True)
    os.makedirs(os.path.join(share, "sub"), exist_ok=True)

    payload = os.urandom(300 * 1024)
    with open(os.path.join(share, "hello.bin"), "wb") as fh:
        fh.write(payload)
    with open(os.path.join(share, "notes.txt"), "wb") as fh:
        fh.write(b"KeShare e2e\n")
    with open(os.path.join(share, "sub", "inner.bin"), "wb") as fh:
        fh.write(os.urandom(1024))

    ios_port = free_port()
    win_port = free_port()

    logger = log_mod.get_log()
    config = config_mod.Config(path=os.path.join(work, "settings.json"))
    config.data["network"]["port"] = win_port
    config.data["share"]["root"] = os.path.join(work, "win-share")
    os.makedirs(config.data["share"]["root"], exist_ok=True)
    config.save()

    ios = fake_ios.FakeIOS(share, name="iPhone-E2E", port=ios_port)
    ios.ctl_peer_port = win_port
    threading.Thread(target=ios.announce_loop, daemon=True).start()
    threading.Thread(target=ios.serve, daemon=True).start()

    server = KSServer(config, logger, resolve_ip=None)
    try:
        server.start()
    except Exception as exc:
        print("服务端启动失败:", exc)
        return 1
    time.sleep(0.8)

    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("  ✔ " if cond else "  �’ ") + name + (("  " + detail) if detail else ""))
        if not cond:
            ok = False

    print("== 1) 握手 ==")
    client = None
    try:
        client = KSClient(config, "127.0.0.1", ios_port, log=logger,
                          local_port=win_port, resolve_ip=None)
        client.connect(timeout=8)
        check("连接并握手成功", True, "对端=%s" % (client.peer_name,))
        check("token 已协商", bool(client.session.token))
    except Exception as exc:
        check("连接并握手成功", False, repr(exc))
        traceback.print_exc()
        print(">>> 到这里就失败了，后续跳过")
        server.stop()
        return 1

    print("== 2) 列目录 ==")
    try:
        listing = client.list_files("")
        files = listing.get("files") or []
        names = sorted(f["name"] for f in files)
        check("返回文件列表", "hello.bin" in names, str(names))
        check("识别目录", any(f.get("kind") == "dir" for f in files))
    except Exception as exc:
        check("返回文件列表", False, repr(exc))
        traceback.print_exc()

    print("== 3) 下载 + 校验 ==")
    try:
        dest = os.path.join(work, "downloaded.bin")
        client.get("hello.bin", dest_path=dest, timeout=30)
        got = open(dest, "rb").read()
        check("大小一致", len(got) == len(payload), "%d vs %d" % (len(got), len(payload)))
        check("内容一致", got == payload)
        check("SHA-KE-1024 一致",
              ke1024.sha_ke_1024(got) == ke1024.sha_ke_1024(payload))
    except Exception as exc:
        check("下载完成", False, repr(exc))
        traceback.print_exc()

    print("== 4) 上传（推送）==")
    try:
        local = os.path.join(work, "push.txt")
        with open(local, "wb") as fh:
            fh.write(b"push from windows\n" * 100)
        client.push(local, timeout=30)
        check("推送完成", True)
    except Exception as exc:
        check("推送完成", False, repr(exc))
        traceback.print_exc()

    try:
        client.close()
    except Exception:
        pass
    server.stop()
    print("\n结果:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
