# -*- coding: utf-8 -*-
"""兼容层：旧的 SHA-KE-1024 接口现在指向标准 SHA-256。

1.0.4 起完整性算法统一为 SHA-256（iOS 用 CommonCrypto，Windows 用 hashlib，
两端都有原生加速）。保留本模块只是为了不破坏旧引用。
"""
from __future__ import annotations

from .hashlib_sha import Sha256 as Ke1024
from .hashlib_sha import sha256 as sha_ke_1024
from .hashlib_sha import sha256_file as sha_ke_1024_file
from .hashlib_sha import sha256_hex as sha_ke_1024_hex

DIGEST_BYTES = 32
DIGEST_HEX = 64

VECTORS = (
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (b"KeShare protocol v1",
     "2ce62cf9153ff7c242f930f50321101ac28154ac80f17deeb145702803bc0f5a"),
    (bytes(range(256)) * 3,
     "f3a25aa93aa2fbba28d79260535bbd6a5eb0fc1c24a8b0f04e12b484c1dfe363"),
)