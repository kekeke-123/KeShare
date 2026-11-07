# -*- coding: utf-8 -*-
"""settings.json handling for KeShare (``%APPDATA%\\KeShare\\settings.json``).

Rules
-----
* missing keys are filled from :data:`DEFAULTS` on load
* unknown keys are preserved on save
* the file is UTF-8, pretty printed (indent 2) with sorted keys
* ``device.id`` / ``device.secret`` are generated exactly once
* saves are atomic (write temp file, then :func:`os.replace`)

The ``KES_SETTINGS_PATH`` environment variable overrides the location; the unit
tests use it (through :meth:`Config.load`) to stay out of the real profile.
"""

import copy
import json
import os
import secrets
import tempfile

from . import KSAPP_VERSION

SETTINGS_ENV = "KES_SETTINGS_PATH"
APP_DIR_NAME = "KeShare"

DEFAULTS = {
    "version": 1,
    "device": {
        "name": "Windows-PC",
        "id": "",
        "secret": "",
    },
    "share": {
        "root": "",
        "receive_dir": "Received",
        "allow_upload": True,
        "allow_clipboard": True,
        "max_speed_kbps": 0,
        "max_file_mb": 2048,
        "chunk_kb": 1024,
    },
    "network": {
        "port": 47377,
        "auto_accept_pairing": False,
        "discovery": "both",
        "bind_address": "0.0.0.0",
        "web_access": True,
    },
    "ui": {
        "theme": "dark",
        "language": "zh-CN",
        "start_minimized": False,
    },
    "developer": {
        "unlocked": False,
        "verbose_logging": False,
        "log_ring_size": 2000,
    },
}


def app_dir():
    """Return ``%APPDATA%\\KeShare`` (falling back to ``~/.keshare``)."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def settings_path():
    """Full path of ``settings.json``."""
    override = os.environ.get(SETTINGS_ENV)
    if override:
        return os.path.abspath(override)
    return os.path.join(app_dir(), "settings.json")


def default_share_root():
    """``~/KeShare`` with forward slashes (matching the documented JSON)."""
    return os.path.expanduser("~").replace("\\", "/").rstrip("/") + "/KeShare"


def default_settings():
    """A fresh default settings dict (new device id + secret, never reused)."""
    data = copy.deepcopy(DEFAULTS)
    data["device"]["id"] = secrets.token_hex(8).upper()
    data["device"]["secret"] = secrets.token_hex(32)
    data["share"]["root"] = default_share_root()
    return data


def deep_merge(base, override):
    """Recursively merge *override* into *base*; unknown keys survive."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _as_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return default


def _as_int(value, default, lo=None, hi=None):
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None and out < lo:
        out = lo
    if hi is not None and out > hi:
        out = hi
    return out


def _clean_name(value, default="Windows-PC"):
    text = str(value or "").strip()
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    if not text:
        return default
    return text[:64]


class Config(object):
    """Dict-backed settings object with dotted accessors."""

    def __init__(self, data=None, path=None):
        self.path = path or settings_path()
        self.data = deep_merge(DEFAULTS, data or {})
        # 无论从哪条路径构造，都保证身份标识存在：
        # 空 did 会让两端 token 计算不一致，握手必然失败。
        if not str(self.data.get("device", {}).get("id") or "").strip():
            self.data.setdefault("device", {})["id"] = secrets.token_hex(8).upper()
        if not str(self.data.get("device", {}).get("secret") or "").strip():
            self.data.setdefault("device", {})["secret"] = secrets.token_hex(32)

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls, path=None):
        """Load settings, creating the file with defaults when it is missing."""
        target = path or settings_path()
        data = None
        try:
            with open(target, "rb") as handle:
                raw = handle.read()
            if raw.strip():
                data = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(data, dict):
                    data = None
        except (OSError, ValueError):
            data = None

        if data is None:
            cfg = cls(default_settings(), target)
            cfg.normalise()
            try:
                cfg.save()
            except OSError:
                pass
            return cfg

        cfg = cls(data, target)
        generated = False
        if not cfg.device_id:
            cfg.data["device"]["id"] = secrets.token_hex(8).upper()
            generated = True
        if not cfg.secret_hex:
            cfg.data["device"]["secret"] = secrets.token_hex(32)
            generated = True
        if not cfg.get("share.root"):
            cfg.data["share"]["root"] = default_share_root()
            generated = True
        cfg.normalise()
        if generated:
            try:
                cfg.save()
            except OSError:
                pass
        return cfg

    # -- persistence -------------------------------------------------------
    def as_dict(self):
        return copy.deepcopy(self.data)

    def save(self, path=None):
        """Write the JSON file atomically (UTF-8, indent 2, sorted keys)."""
        target = path or self.path
        folder = os.path.dirname(os.path.abspath(target))
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError:
                pass
        text = json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_fd = None
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(prefix=".settings-", suffix=".tmp",
                                                dir=folder or None)
            with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="\n") as handle:
                tmp_fd = None
                handle.write(text + "\n")
            os.replace(tmp_path, target)
            tmp_path = None
        finally:
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        self.path = target
        return target

    # -- generic access ----------------------------------------------------
    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted, value):
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
        return value

    # -- validation --------------------------------------------------------
    def normalise(self):
        """Clamp/sanitise every value that reaches sockets, filesystem or UI."""
        d = self.data
        for section, defaults in DEFAULTS.items():
            if not isinstance(d.get(section), dict):
                d[section] = copy.deepcopy(defaults)
            if section == "version":
                continue
            for key, value in defaults.items():
                d[section].setdefault(key, copy.deepcopy(value))

        d["version"] = _as_int(d.get("version"), 1, 1, 1)
        d["device"]["name"] = _clean_name(d["device"].get("name"))
        did = str(d["device"].get("id") or "").strip().upper()
        did = "".join(ch for ch in did if ch in "0123456789ABCDEF")
        d["device"]["id"] = did[:16]
        secret = str(d["device"].get("secret") or "").strip().lower()
        d["device"]["secret"] = "".join(ch for ch in secret if ch in "0123456789abcdef")[:64]

        share = d["share"]
        root = str(share.get("root") or "").strip() or default_share_root()
        share["root"] = root.replace("\\", "/")
        rdir = str(share.get("receive_dir") or "Received").strip() or "Received"
        share["receive_dir"] = rdir.replace("\\", "/")
        share["allow_upload"] = _as_bool(share.get("allow_upload"), True)
        share["allow_clipboard"] = _as_bool(share.get("allow_clipboard"), True)
        share["max_speed_kbps"] = _as_int(share.get("max_speed_kbps"), 0, 0, 1048576)
        share["max_file_mb"] = _as_int(share.get("max_file_mb"), 2048, 1, 1048576)
        share["chunk_kb"] = _as_int(share.get("chunk_kb"), 1024, 4, 4096)

        net = d["network"]
        net["port"] = _as_int(net.get("port"), 47377, 1, 65535)
        net["auto_accept_pairing"] = _as_bool(net.get("auto_accept_pairing"), False)
        disc = str(net.get("discovery") or "both").strip().lower()
        net["discovery"] = disc if disc in ("both", "udp", "mdns", "off") else "both"
        bind = str(net.get("bind_address") or "0.0.0.0").strip() or "0.0.0.0"
        net["bind_address"] = bind

        ui = d["ui"]
        theme = str(ui.get("theme") or "dark").strip().lower()
        ui["theme"] = theme if theme in ("dark", "light") else "dark"
        ui["language"] = str(ui.get("language") or "zh-CN")[:16]
        ui["start_minimized"] = _as_bool(ui.get("start_minimized"), False)

        dev = d["developer"]
        dev["unlocked"] = _as_bool(dev.get("unlocked"), False)
        dev["verbose_logging"] = _as_bool(dev.get("verbose_logging"), False)
        dev["log_ring_size"] = _as_int(dev.get("log_ring_size"), 2000, 100, 100000)
        return self

    # -- typed shortcuts ---------------------------------------------------
    @property
    def device_id(self):
        return self.data["device"]["id"]

    @property
    def device_name(self):
        return self.data["device"]["name"]

    @property
    def secret_hex(self):
        return self.data["device"]["secret"]

    @property
    def share_root(self):
        return self.data["share"]["root"]

    @property
    def receive_dir(self):
        rel = self.data["share"]["receive_dir"]
        if os.path.isabs(rel):
            return os.path.normpath(rel)
        return os.path.normpath(os.path.join(self.share_root, rel))

    @property
    def max_speed_kbps(self):
        return int(self.data["share"]["max_speed_kbps"])

    @property
    def max_file_bytes(self):
        return int(self.data["share"]["max_file_mb"]) * 1024 * 1024

    @property
    def chunk_bytes(self):
        return max(1024, int(self.data["share"]["chunk_kb"]) * 1024)

    @property
    def port(self):
        return int(self.data["network"]["port"])

    @property
    def bind_address(self):
        return self.data["network"]["bind_address"]

    @property
    def auto_accept_pairing(self):
        return bool(self.data["network"]["auto_accept_pairing"])

    @property
    def discovery_mode(self):
        return self.data["network"]["discovery"]

    @property
    def web_access(self):
        """局域网 HTTP/WebSocket 通道开关（浏览器直接传文件，不需要热点）。"""
        return bool(self.data["network"].get("web_access", True))

    @property
    def allow_upload(self):
        return bool(self.data["share"]["allow_upload"])

    @property
    def allow_clipboard(self):
        return bool(self.data["share"]["allow_clipboard"])

    @property
    def theme(self):
        return self.data["ui"]["theme"]

    @property
    def developer_locked(self):
        return bool(self.data.get("developer", {}).get("locked"))

    @property
    def developer_unlocked(self):
        return bool(self.data["developer"]["unlocked"])

    @property
    def verbose_logging(self):
        return bool(self.data["developer"]["verbose_logging"])

    @property
    def log_ring_size(self):
        return int(self.data["developer"]["log_ring_size"])

    # -- helpers -----------------------------------------------------------
    def ensure_dirs(self):
        """Create the share root and receive directory when missing."""
        made = []
        for path in (self.share_root, self.receive_dir):
            if not path:
                continue
            try:
                if not os.path.isdir(path):
                    os.makedirs(path)
                    made.append(path)
            except OSError:
                pass
        return made

    def version_label(self):
        return "KeShare %s" % (KSAPP_VERSION,)

    def __getitem__(self, key):
        return self.data[key]

    def __repr__(self):
        return "<Config %s id=%s port=%s>" % (
            self.path, self.device_id, self.port)
