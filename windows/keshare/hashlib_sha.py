# -*- coding: utf-8 -*-
"""文件完整性摘要：SHA-256（标准库 hashlib，带原生加速）。

历史说明：1.0.0–1.0.3 早期用过自研的 SHA-KE-1024，因两端一致性与性能都不划算，
现已统一回标准 SHA-256（256 位 / 64 个十六进制字符）。
"""
from __future__ import annotations

import hashlib

CHUNK = 1024 * 1024


def sha256(data):
    return hashlib.sha256(data).digest()


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk=CHUNK):
    """流式计算文件摘要，返回 64 个十六进制字符。"""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


class Sha256(object):
    """增量接口，便于边传边算。"""

    def __init__(self):
        self._h = hashlib.sha256()
        self.total = 0

    def update(self, data):
        if data:
            self.total += len(data)
            self._h.update(data)
        return self

    def digest(self):
        return self._h.digest()

    def hexdigest(self):
        return self._h.hexdigest()
