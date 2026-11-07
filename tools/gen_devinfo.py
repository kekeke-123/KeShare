# -*- coding: utf-8 -*-
"""生成开发者模式密码的密文（构建期工具，不随应用运行）。

算法：keystream = SHA-256(key)，cipher = plain XOR keystream（循环使用）。
两端都用系统自带 SHA-256，实现一致、无需第三方库。
只能算作轻量混淆（防止密码以明文出现在二进制里被直接 grep），
不是强加密——这一点在 SECURITY.md 里写清楚。
"""
from __future__ import annotations

import hashlib


def xor_stream(data, key):
    stream = hashlib.sha256(key).digest()
    return bytes(b ^ stream[i % len(stream)] for i, b in enumerate(data))


def main():
    plain = "Dev-KeSha8re-26123Kernelcix"
    key = b"KeShare576"
    raw = plain.encode("utf-8")
    cipher = xor_stream(raw, key)
    print("plain length :", len(raw))
    print("cipher hex   :", cipher.hex())
    print("cipher bytes :", ", ".join("0x%02x" % b for b in cipher))
    back = xor_stream(cipher, key).decode("utf-8")
    print("roundtrip    :", "OK" if back == plain else "FAILED")
    # 断言密码复杂度
    has_upper = any(c.isupper() for c in plain)
    has_lower = any(c.islower() for c in plain)
    has_digit = any(c.isdigit() for c in plain)
    has_symbol = any(not c.isalnum() for c in plain)
    print("upper/lower/digit/symbol:", has_upper, has_lower, has_digit, has_symbol)


if __name__ == "__main__":
    main()
