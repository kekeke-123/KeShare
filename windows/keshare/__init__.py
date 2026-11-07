# -*- coding: utf-8 -*-
"""KeShare -- LAN file sharing (Windows 1.0 client).

Pure Python 3 (3.8+ target, degrades to 3.6), standard library + tkinter only.

Architecture
------------
Two channels, both on port 47377:

* **CTL** -- JSON control messages (``len:u32 | UTF-8 JSON``), carried over
  Bluetooth RFCOMM when the Python build supports ``AF_BTH``, otherwise over
  WiFi TCP (the "WiFi 回退" shown in the UI).
* **DATA** -- WiFi TCP, file bytes only: ``DATA <token> <id>\\n`` followed by
  ``seq:u32 | offset:u64 | len:u32 | payload`` frames.  Per PROTOCOL.md
  section 4.0 the **receiver** opens this connection and the sender writes.

No encryption: integrity comes from **SHA-KE-1024** (128 byte / 256 hex
digest), declared by the sender in the final ``hash`` control message and
checked by the receiver, which answers ``hash_resp``.

Modules
-------
``protocol``   CTL JSON framing, DATA chunk framing, CTL message builders
``ke1024``     SHA-KE-1024 implementation (port of protocol/sha_ke_1024.py)
``crypto``     SHA-256 token derivation + SHA-KE-1024 re-exports
``ctl``        CTL transports (Bluetooth RFCOMM / WiFi TCP) and the handshake
``data``       DATA channel streaming, SHA-KE-1024 checks, transfer registry
``config``     ``%APPDATA%\\KeShare\\settings.json`` load/save with defaults
``log``        in-memory ring buffer logger with TXT export
``files``      share-root sandboxing, path validation, directory listing
``clipboard``  Win32 text clipboard access through :mod:`ctypes`
``discovery``  UDP probing (port 47377) + minimal mDNS on 224.0.0.251:5353
``mdns``       PTR/SRV/TXT encoder and parser used by ``discovery``
``diag``       runtime diagnostics for the developer tab
``server``     TCP listener on 47377 (CTL sessions + sender-side DATA)
``client``     CTL client: hello, get (download), push (upload)
``ui``         tkinter user interface
"""

KSAPP_NAME = "KeShare"
KSAPP_VERSION = "1.0.0"
KSPROTO_VERSION = 1
KS_PORT = 47377

__all__ = [
    "KSAPP_NAME", "KSAPP_VERSION", "KSPROTO_VERSION", "KS_PORT",
]
