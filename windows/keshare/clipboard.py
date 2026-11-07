# -*- coding: utf-8 -*-
"""Win32 text clipboard access through :mod:`ctypes` (no third party deps).

KeShare only ever exchanges plain Unicode text (``CF_UNICODETEXT``).  All calls
are defensive: on a non-Windows host, or when another process holds the
clipboard open, they raise :class:`ClipboardError` and the caller answers
``err:"unsupported"``/``"io"`` instead of crashing.
"""

import ctypes
import sys

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

MAX_TEXT_BYTES = 64 * 1024          # PROTOCOL.md section 4.7

_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:                                             # pragma: no cover - os specific
    try:
        _user32 = ctypes.windll.user32
        _kernel32 = ctypes.windll.kernel32
        _kernel32.GlobalAlloc.restype = ctypes.c_void_p
        _kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        _kernel32.GlobalLock.restype = ctypes.c_void_p
        _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        _kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        _user32.GetClipboardData.restype = ctypes.c_void_p
        _user32.GetClipboardData.argtypes = [ctypes.c_uint]
        _user32.SetClipboardData.restype = ctypes.c_void_p
        _user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        _user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        _user32.EmptyClipboard.argtypes = []
        _user32.CloseClipboard.argtypes = []
        _AVAILABLE = True
    except Exception:                                       # pragma: no cover
        _AVAILABLE = False
else:                                                       # pragma: no cover
    _user32 = None
    _kernel32 = None
    _AVAILABLE = False


class ClipboardError(Exception):
    """Clipboard unavailable or access denied."""


def available():
    return _AVAILABLE


def _open(retries=5):
    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return True
    return False


def get_text():
    """Return the clipboard text (``""`` when empty); raises ClipboardError."""
    if not _AVAILABLE:
        raise ClipboardError("clipboard not supported on this platform")
    if not _open():
        raise ClipboardError("cannot open clipboard")
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            text = ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
        return text or ""
    finally:
        try:
            _user32.CloseClipboard()
        except Exception:
            pass


def set_text(text):
    """Replace the clipboard text; raises ClipboardError."""
    if not _AVAILABLE:
        raise ClipboardError("clipboard not supported on this platform")
    data = str(text or "")
    encoded = data.encode("utf-16-le") + b"\x00\x00"
    if not _open():
        raise ClipboardError("cannot open clipboard")
    try:
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard failed")
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not handle:
            raise ClipboardError("GlobalAlloc failed")
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            _kernel32.GlobalFree(handle)
            raise ClipboardError("GlobalLock failed")
        try:
            ctypes.memmove(pointer, encoded, len(encoded))
        finally:
            _kernel32.GlobalUnlock(handle)
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            raise ClipboardError("SetClipboardData failed")
        return True
    finally:
        try:
            _user32.CloseClipboard()
        except Exception:
            pass


def truncate(text, limit=MAX_TEXT_BYTES):
    """Clamp clipboard text to the protocol limit; returns ``(text, truncated)``."""
    data = str(text or "")
    raw = data.encode("utf-8", "replace")
    if len(raw) <= limit:
        return data, False
    cut = raw[:limit]
    while cut:
        try:
            return cut.decode("utf-8"), True
        except UnicodeDecodeError:
            cut = cut[:-1]
    return "", True
