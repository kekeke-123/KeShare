# -*- coding: utf-8 -*-
"""Share-root sandboxing, path validation and directory listing.

Every path that arrives from the network passes through
:func:`normalise_rel` / :class:`ShareRoot` before touching the filesystem:

* NFC normalisation, ``/`` separators
* rejects absolute paths, drive letters, ``..`` and ``.`` components
* rejects Windows reserved names (``CON``, ``NUL``, ``AUX``, ``PRN``,
  ``COM1``-``COM9``, ``LPT1``-``LPT9``, also with any extension)
* rejects control characters, ``<>:"|?*`` and components ending in ``.``/space
* rejects paths longer than 1024 bytes (UTF-8)
* re-checks the resolved real path stays inside the share root
"""

import os
import re
import threading
import time
import unicodedata

from . import crypto

MAX_REL_BYTES = 1024

_RESERVED = set([
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
])
_BAD_CHARS = set('<>:"|?*')
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class SandboxError(ValueError):
    """Raised when a path is rejected by the sandbox rules."""

    def __init__(self, message, err="denied"):
        ValueError.__init__(self, message)
        self.err = err


def file_id(rel_path):
    """``hex(sha256(path))[:16]`` as used by LIST/STAT."""
    return crypto.sha256_hex(rel_path.encode("utf-8"))[:16]


def _clean_component(part):
    if not part:
        raise SandboxError("empty path component")
    if part in (".", ".."):
        raise SandboxError("relative path component %r is not allowed" % (part,))
    if len(part) > 255:
        raise SandboxError("path component too long")
    for ch in part:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise SandboxError("control character in path")
        if ch in _BAD_CHARS:
            raise SandboxError("illegal character %r in path" % (ch,))
    if part.rstrip(" .") != part:
        raise SandboxError("path component may not end with space or dot")
    stem = part.split(".", 1)[0].strip().upper()
    if stem in _RESERVED:
        raise SandboxError("reserved device name %r" % (part,))
    return part


def normalise_rel(path, allow_empty=True):
    """Return a clean POSIX relative path or raise :class:`SandboxError`."""
    if path is None:
        raise SandboxError("missing path")
    if isinstance(path, bytes):
        try:
            path = path.decode("utf-8")
        except UnicodeDecodeError:
            raise SandboxError("path is not valid UTF-8")
    if not isinstance(path, str):
        raise SandboxError("path must be a string")
    text = unicodedata.normalize("NFC", path)
    if "\x00" in text:
        raise SandboxError("NUL in path")
    if len(text.encode("utf-8", "replace")) > MAX_REL_BYTES:
        raise SandboxError("path too long", "too_large")
    text = text.replace("\\", "/")
    if not text or text == "/":
        if allow_empty:
            return ""
        raise SandboxError("empty path")
    if text.startswith("/") or _DRIVE_RE.match(text):
        raise SandboxError("absolute paths are not allowed")
    parts = []
    for raw in text.split("/"):
        if not raw:
            raise SandboxError("empty path component")
        parts.append(_clean_component(raw))
    if not parts and not allow_empty:
        raise SandboxError("empty path")
    return "/".join(parts)


def is_safe_rel(path):
    """Boolean variant of :func:`normalise_rel`."""
    try:
        normalise_rel(path)
        return True
    except SandboxError:
        return False


def basename_of(rel_path):
    return rel_path.rsplit("/", 1)[-1] if rel_path else ""


def safe_filename(name, fallback="file.bin"):
    """Sanitise a *single* file name used for incoming files (no directories)."""
    if isinstance(name, bytes):
        try:
            name = name.decode("utf-8")
        except UnicodeDecodeError:
            name = fallback
    text = unicodedata.normalize("NFC", str(name or "")).strip()
    text = text.replace("\\", "_").replace("/", "_")
    cleaned = []
    for ch in text:
        if ord(ch) < 0x20 or ord(ch) == 0x7F or ch in _BAD_CHARS:
            cleaned.append("_")
        else:
            cleaned.append(ch)
    text = "".join(cleaned).strip(" .")
    if not text or text in (".", ".."):
        text = fallback
    stem = text.split(".", 1)[0].strip().upper()
    if stem in _RESERVED:
        text = "_" + text
    if len(text) > 200:
        root, ext = os.path.splitext(text)
        text = root[:200 - len(ext)] + ext
    return text


def unique_path(folder, filename):
    """Return a non-existing path inside *folder* (``name (1).ext`` style)."""
    filename = safe_filename(filename)
    candidate = os.path.join(folder, filename)
    if not os.path.exists(candidate):
        return candidate
    root, ext = os.path.splitext(filename)
    for index in range(1, 10000):
        candidate = os.path.join(folder, "%s (%d)%s" % (root, index, ext))
        if not os.path.exists(candidate):
            return candidate
    return os.path.join(folder, "%s-%d%s" % (root, int(time.time()), ext))


class ShareRoot(object):
    """A directory that is the only place files may be read from."""

    def __init__(self, root):
        self.root = os.path.abspath(str(root))
        self._hash_cache = {}
        self._lock = threading.Lock()

    # -- resolution --------------------------------------------------------
    def ensure(self):
        if not os.path.isdir(self.root):
            try:
                os.makedirs(self.root)
            except OSError:
                pass
        return self.root

    def resolve(self, rel_path):
        """Map a wire path to an absolute path inside the share root."""
        rel = normalise_rel(rel_path)
        target = os.path.abspath(os.path.join(self.root, *rel.split("/")) if rel else self.root)
        root_norm = os.path.normcase(self.root.rstrip("\\/"))
        target_norm = os.path.normcase(target)
        if target_norm != root_norm and not target_norm.startswith(root_norm + os.sep):
            raise SandboxError("path escapes the share root")
        # Symlink check: the real path must stay inside the real root as well.
        try:
            real_root = os.path.normcase(os.path.realpath(self.root))
            real_target = os.path.normcase(os.path.realpath(target))
            if (real_target != real_root
                    and not real_target.startswith(real_root + os.sep)):
                raise SandboxError("path escapes the share root (symlink)")
        except OSError:
            pass
        return target

    # -- listing -----------------------------------------------------------
    def list_dir(self, rel_path="", max_items=200, offset=0):
        """List one directory level (directories first, then files by name)."""
        target = self.resolve(rel_path)
        if not os.path.isdir(target):
            raise FileNotFoundError("no such directory: %s" % (rel_path or "/",))
        max_items = max(1, min(int(max_items or 200), 2000))
        offset = max(0, int(offset or 0))
        base_rel = normalise_rel(rel_path)
        entries = []
        try:
            names = os.listdir(target)
        except OSError:
            raise
        for name in names:
            full = os.path.join(target, name)
            child_rel = (base_rel + "/" + name) if base_rel else name
            try:
                info = os.stat(full)
            except OSError:
                continue
            is_dir = os.path.isdir(full)
            entry = {
                "path": child_rel,
                "name": name,
                "kind": "dir" if is_dir else "file",
                "mtime": int(info.st_mtime),
                "id": file_id(child_rel),
            }
            if not is_dir:
                entry["size"] = int(info.st_size)
            entries.append(entry)
        entries.sort(key=lambda e: (0 if e["kind"] == "dir" else 1,
                                    e["name"].lower()))
        total = len(entries)
        window = entries[offset:offset + max_items]
        return {"total": total, "offset": offset, "files": window}

    def stat(self, rel_path):
        """Return size/mtime/hash for a file (or a minimal dict for a dir).

        ``hash`` is the SHA-KE-1024 digest (256 hex chars) used by the protocol.
        """
        target = self.resolve(rel_path)
        info = os.stat(target)
        rel = normalise_rel(rel_path)
        if os.path.isdir(target):
            return {
                "path": rel,
                "kind": "dir",
                "size": 0,
                "mtime": int(info.st_mtime),
                "hash": "",
            }
        return {
            "path": rel,
            "kind": "file",
            "size": int(info.st_size),
            "mtime": int(info.st_mtime),
            "hash": self.digest_of(rel),
        }

    def digest_of(self, rel_path):
        """SHA-KE-1024 of a file, cached by (path, size, mtime)."""
        target = self.resolve(rel_path)
        info = os.stat(target)
        if os.path.isdir(target):
            return ""
        key = (os.path.normcase(target), int(info.st_size), int(info.st_mtime))
        with self._lock:
            cached = self._hash_cache.get(key)
        if cached:
            return cached
        digest = crypto.sha_ke_1024_file(target)
        with self._lock:
            if len(self._hash_cache) > 64:
                self._hash_cache.clear()
            self._hash_cache[key] = digest
        return digest

    #: Backwards compatible alias (the protocol digest used to be SHA-256).
    sha256_of = digest_of

    # -- writing -----------------------------------------------------------
    def receive_target(self, dest_rel, filename):
        """Pick a unique path for an incoming file under *dest_rel* inside the root."""
        dest = normalise_rel(dest_rel or "", allow_empty=True)
        folder = self.resolve(dest)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        name = safe_filename(filename)
        return unique_path(folder, name)

    def rel_of(self, abs_path):
        """Convert an absolute path back to a wire (POSIX) relative path."""
        rel = os.path.relpath(abs_path, self.root)
        return rel.replace("\\", "/")
