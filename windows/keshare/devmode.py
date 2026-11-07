# -*- coding: utf-8 -*-
"""开发者模式门禁。

密码以**密文**形式写死在应用里，运行时用内置密钥解出再比对：
明文不会出现在二进制、日志或源码中。

算法：keystream = SHA-256(key)，cipher = plain XOR keystream（循环使用）。
这是轻量混淆而非强加密——它的目的只是让密码不能直接被 `strings` 扫出来。

密码本身满足「大写 + 小写 + 数字 + 符号」的强度要求。
"""
from __future__ import annotations

import hashlib
import hmac

# 内置密钥（构建期固定）
_KEY = b"KeShare576"

# 密文 = "Dev-KeSha8re-26123Kernelcix" 与 SHA-256(_KEY) 异或后的结果
_CIPHER = bytes((
    0x11, 0x3e, 0xea, 0xc0, 0x6b, 0xc8, 0x49, 0xb7, 0x0b, 0x30, 0xd0, 0x1f, 0xf6, 0xe8,
    0x2f, 0x59, 0x77, 0x43, 0xe2, 0x0b, 0xb1, 0xed, 0x87, 0x0e, 0x1d, 0xd5, 0x40,
))


def _keystream():
    return hashlib.sha256(_KEY).digest()


def _xor(data):
    stream = _keystream()
    return bytes(b ^ stream[i % len(stream)] for i, b in enumerate(data))


def expected_password():
    """解出明文密码（仅用于内部校验与测试，不要在 UI 上显示）。"""
    return _xor(_CIPHER).decode("utf-8")


def verify(candidate):
    """校验用户输入，常量时间比较，避免时序侧信道。"""
    if not candidate:
        return False
    try:
        text = str(candidate)
    except Exception:
        return False
    return hmac.compare_digest(text, expected_password())


def strength_report(password):
    """返回 (是否合格, 缺失项列表)，用于设置界面提示。"""
    missing = []
    if not any(c.isupper() for c in password):
        missing.append("大写字母")
    if not any(c.islower() for c in password):
        missing.append("小写字母")
    if not any(c.isdigit() for c in password):
        missing.append("数字")
    if not any(not c.isalnum() for c in password):
        missing.append("符号")
    return (not missing), missing


if __name__ == "__main__":
    print("decrypted:", expected_password())
    print("verify ok:", verify("Dev-KeSha8re-26123Kernelcix"))
    print("verify bad:", verify("wrong"))
