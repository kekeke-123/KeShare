# -*- coding: utf-8 -*-
"""Ring-buffer logger with TXT export.

* keeps the last ``log_ring_size`` lines in memory (developer panel)
* appends to ``%APPDATA%\\KeShare\\keshare.log`` with simple size rotation
* notifies registered listeners (the tkinter UI) from any thread
* never logs clipboard text or file contents -- callers pass only metadata
"""

import datetime
import os
import threading

from . import KSAPP_NAME, KSAPP_VERSION
from . import config as ksconfig

DEBUG = "DEBUG"
INFO = "INFO"
WARN = "WARN"
ERROR = "ERROR"

LEVELS = (DEBUG, INFO, WARN, ERROR)

MAX_FILE_BYTES = 2 * 1024 * 1024


def timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Log(object):
    """Thread safe ring buffer + file logger."""

    def __init__(self, path=None, ring_size=2000, verbose=False):
        self._lock = threading.RLock()
        self._lines = []
        self._ring_size = max(100, int(ring_size or 2000))
        self._listeners = []
        self.verbose = bool(verbose)
        self.protocol_debug = False
        self.path = path or os.path.join(ksconfig.app_dir(), "keshare.log")
        self.file_enabled = True
        self._prepare_file()

    # -- configuration -----------------------------------------------------
    def _prepare_file(self):
        folder = os.path.dirname(os.path.abspath(self.path))
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError:
                self.file_enabled = False

    def set_ring_size(self, size):
        with self._lock:
            self._ring_size = max(100, int(size or 2000))
            if len(self._lines) > self._ring_size:
                self._lines = self._lines[-self._ring_size:]
        return self._ring_size

    def set_verbose(self, flag):
        with self._lock:
            self.verbose = bool(flag)
            if self.verbose:
                self.protocol_debug = True
        return self.verbose

    def set_protocol_debug(self, flag):
        with self._lock:
            self.protocol_debug = bool(flag)
        return self.protocol_debug

    @property
    def ring_size(self):
        return self._ring_size

    # -- listeners ---------------------------------------------------------
    def add_listener(self, callback):
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify(self, line):
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(line)
            except Exception:
                pass

    # -- core --------------------------------------------------------------
    def log(self, level, *parts, **kwargs):
        """Append one line; *parts* are str()'d and joined with a space.

        ``force=True`` bypasses the verbose filter (used for protocol tracing,
        which has its own toggle in the developer tab).
        """
        level = str(level).upper()
        if level not in LEVELS:
            level = INFO
        force = bool(kwargs.get("force", False))
        if level == DEBUG and not (self.verbose or force):
            return None
        text = " ".join(_stringify(p) for p in parts)
        line = "%s [%s] %s" % (timestamp(), level, text)
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self._ring_size:
                del self._lines[:len(self._lines) - self._ring_size]
            enabled = self.file_enabled
        if enabled:
            self._write(line)
        self._notify(line)
        return line

    def _write(self, line):
        try:
            if os.path.getsize(self.path) > MAX_FILE_BYTES:
                backup = self.path + ".1"
                try:
                    if os.path.exists(backup):
                        os.remove(backup)
                    os.replace(self.path, backup)
                except OSError:
                    pass
        except OSError:
            pass
        try:
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        except OSError:
            self.file_enabled = False

    # -- levels ------------------------------------------------------------
    def debug(self, *parts):
        return self.log(DEBUG, *parts)

    def info(self, *parts):
        return self.log(INFO, *parts)

    def warn(self, *parts):
        return self.log(WARN, *parts)
    warning = warn

    def error(self, *parts):
        return self.log(ERROR, *parts)


    # -- protocol tracing --------------------------------------------------
    def protocol(self, *parts):
        """Log a CTL/DATA frame when the developer toggle is enabled."""
        with self._lock:
            enabled = self.protocol_debug
        if not enabled:
            return None
        return self.log(DEBUG, "ctl", *parts, force=True)

    # -- reading -----------------------------------------------------------
    def lines(self, count=None):
        """Return up to *count* most recent lines (all when ``count`` is None)."""
        with self._lock:
            if count is None or count <= 0:
                return list(self._lines)
            return list(self._lines[-int(count):])

    def text(self, count=None):
        return "\n".join(self.lines(count))

    def clear(self):
        with self._lock:
            self._lines = []

    # -- export ------------------------------------------------------------
    def export_txt(self, path, header=True):
        """Write the ring buffer (plus a header) to *path*; returns the path."""
        folder = os.path.dirname(os.path.abspath(path))
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError:
                pass
        body = []
        if header:
            body.append("# %s %s log export" % (KSAPP_NAME, KSAPP_VERSION))
            body.append("# generated: %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
            body.append("# lines: %d" % (len(self._lines),))
            body.append("")
        body.extend(self.lines())
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(body) + "\n")
        return path


def _stringify(value):
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def redact(text, limit=64):
    """Return a short, content-free description of *text* for logging."""
    if text is None:
        return "None"
    try:
        size = len(text)
    except TypeError:
        return "<value>"
    head = ""
    if size <= 8:
        head = " %r" % (text[:limit],)
    return "<%d chars%s>" % (size, head)


_LOG = None
_LOG_LOCK = threading.Lock()


def get_log():
    """Process wide logger instance (created from the config on first use)."""
    global _LOG
    with _LOG_LOCK:
        if _LOG is None:
            try:
                cfg = ksconfig.Config.load()
                ring = cfg.log_ring_size
                verbose = cfg.verbose_logging
            except Exception:
                ring, verbose = 2000, False
            _LOG = Log(ring_size=ring, verbose=verbose)
        return _LOG


def set_log(logger):
    """Replace the process wide logger (used by tests and the UI)."""
    global _LOG
    with _LOG_LOCK:
        _LOG = logger
    return logger
