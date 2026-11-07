# -*- coding: utf-8 -*-
"""KeShare wire protocol v1 (simplified): CTL JSON messages + DATA chunk frames.

Two channels
------------
**CTL** -- control channel, JSON messages.  Two transports:

* Bluetooth Classic RFCOMM (primary), see :mod:`keshare.ctl`
* WiFi TCP port 47377 (fallback, same bytes), documented in the UI as
  ``控制通道: WiFi 回退``

Framing on CTL is a 4 byte big-endian length followed by that many bytes of
UTF-8 JSON::

    +----------------+----------------------------+
    | len: u32 BE    | JSON payload (UTF-8)       |
    +----------------+----------------------------+

**DATA** -- WiFi TCP port 47377, file bytes only.  A DATA connection starts with
one ASCII line, then a stream of chunk frames::

    DATA <token> <id>\\n
    seq:u32 BE | offset:u64 BE | len:u32 BE | payload[len]
    ... repeated ...
    seq:u32 BE | offset:u64 BE | len:u32 BE = 0      # final frame

The final frame has ``len == 0`` and ``offset == total size``.

Port 47377 carries both kinds of connection; the listener peeks the first four
bytes: ``b"DATA"`` (0x44415441) means the data channel, anything else is a CTL
length prefix (a CTL JSON message is at most 4096 bytes on the wire, so the
discriminator is unambiguous).

Message flow (PROTOCOL.md section 4; the **receiver** opens the DATA connection)::

    A -> B  hello        {v,did,name,kind,osv,port,caps,nonce}
    B -> A  hello_ack    {v,did,name,kind,osv,port,caps,nonce,token}
    A -> B  get          {token,id,path}          # A wants a file from B
    B -> A  get_resp     {id,ok,size,name}
    A == TCP 47377 ==> B                            # A is the receiver, connects
    B -> A  hash         {id,hash,size}             # SHA-KE-1024, 256 hex
    A -> B  hash_resp    {id,ok}                    # ok:false,err:"checksum"

    push (A -> B, B is the receiver):
    A -> B  put_offer    {token,id,name,size}
    B -> A  put_resp     {id,ok}
    B == TCP 47377 ==> A                            # B is the receiver, connects
    A -> B  hash         {id,hash,size}
    B -> A  hash_resp    {id,ok}

    A -> B  bye          {}
"""

import json
import struct
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KS_PORT = 47377
#: PROTOCOL.md section 1.1: one CTL frame is <= 4096 bytes.  We refuse to send
#: anything larger but accept up to CTL_MAX_READ when receiving (tolerant).
CTL_MAX_JSON = 4096
CTL_MAX_READ = 64 * 1024
CHUNK_DEFAULT = 64 * 1024
CHUNK_MAX = 4 * 1024 * 1024
CHUNK_HDR = struct.Struct(">IQI")          # seq, offset, len
DATA_PREFIX = b"DATA"
DATA_LINE_MAX = 160

PROTO_VERSION = 1

#: capabilities bit field (kept from protocol/ks_const.h)
CAP_FILES = 1 << 0
CAP_PASTE = 1 << 1
CAP_ACCEPT_FT = 1 << 2
CAP_RECV_ON = 1 << 3
CAP_BT = 1 << 4
CAP_MULTI = 1 << 5
CAP_RESUME = 1 << 6
CAPS_DEFAULT = (CAP_FILES | CAP_PASTE | CAP_ACCEPT_FT | CAP_RECV_ON
                | CAP_BT | CAP_MULTI | CAP_RESUME)

# CTL message types
M_HELLO = "hello"
M_HELLO_ACK = "hello_ack"
M_LIST = "list"
M_LIST_RESP = "list_resp"
M_STAT = "stat"
M_STAT_RESP = "stat_resp"
M_GET = "get"
M_GET_RESP = "get_resp"
M_PUT_OFFER = "put_offer"
M_PUT_RESP = "put_resp"
M_HASH = "hash"
M_HASH_RESP = "hash_resp"
M_ERR = "err"
M_BYE = "bye"

MESSAGE_TYPES = (M_HELLO, M_HELLO_ACK, M_LIST, M_LIST_RESP, M_STAT,
                 M_STAT_RESP, M_GET, M_GET_RESP, M_PUT_OFFER, M_PUT_RESP,
                 M_HASH, M_HASH_RESP, M_ERR, M_BYE)

ERR_CODES = ("badreq", "notfound", "denied", "bad_order", "checksum",
             "too_large", "io", "unsupported", "bad_token", "no_wifi")


class ProtocolError(Exception):
    """Malformed message / rejected request, with a protocol error code."""

    def __init__(self, message, err="badreq"):
        Exception.__init__(self, message)
        self.err = err


class ConnectionClosed(ProtocolError):
    def __init__(self, message="connection closed"):
        ProtocolError.__init__(self, message, "io")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------
def json_dumps(obj):
    """Compact UTF-8 JSON bytes (non-ASCII stays UTF-8)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def json_loads(raw):
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("message is not valid UTF-8", "badreq")
    else:
        text = raw
    if not text.strip():
        raise ProtocolError("empty message", "badreq")
    try:
        value = json.loads(text)
    except ValueError:
        raise ProtocolError("message is not valid JSON", "badreq")
    if not isinstance(value, dict):
        raise ProtocolError("message must be a JSON object", "badreq")
    return value


def validate_message(msg):
    """Check the envelope (``t`` + optional ``v``) of a CTL message."""
    if not isinstance(msg, dict):
        raise ProtocolError("message must be an object", "badreq")
    kind = msg.get("t")
    if not isinstance(kind, str) or not kind:
        raise ProtocolError("message without t", "badreq")
    version = msg.get("v", PROTO_VERSION)
    if version != PROTO_VERSION:
        raise ProtocolError("unsupported version %r" % (version,), "unsupported")
    return msg


def require_fields(msg, *fields):
    """Raise when a required field is missing/empty."""
    for field in fields:
        if field not in msg or msg[field] in (None, ""):
            raise ProtocolError("missing field %r in %r" % (field, msg.get("t")), "badreq")
    return msg


def encode_ctl(msg):
    """4 byte big-endian length prefix + UTF-8 JSON."""
    raw = json_dumps(msg)
    if len(raw) > CTL_MAX_JSON:
        raise ProtocolError("CTL message too large (%d)" % (len(raw),), "too_large")
    return struct.pack(">I", len(raw)) + raw


class CtlFramer(object):
    """Incremental CTL parser (tolerates split/coalesced TCP reads)."""

    def __init__(self, max_json=CTL_MAX_READ):
        self.max_json = max_json
        self._buf = bytearray()

    @property
    def pending(self):
        return len(self._buf)

    def feed(self, data):
        if data:
            self._buf += data
        if len(self._buf) > 4 + self.max_json:
            raise ProtocolError("CTL buffer overflow", "too_large")

    def clear(self):
        self._buf = bytearray()

    def pop(self):
        """Return the next complete message dict, or ``None``."""
        buf = self._buf
        if len(buf) < 4:
            return None
        (length,) = struct.unpack(">I", bytes(buf[:4]))
        if length > self.max_json:
            raise ProtocolError("CTL message too large (%d)" % (length,), "too_large")
        total = 4 + length
        if len(buf) < total:
            return None
        raw = bytes(buf[4:total])
        del buf[:total]
        return validate_message(json_loads(raw))


# ---------------------------------------------------------------------------
# DATA channel framing
# ---------------------------------------------------------------------------
def encode_chunk(seq, offset, payload):
    """One DATA frame: ``seq:u32``, ``offset:u64``, ``len:u32``, payload."""
    payload = payload or b""
    if len(payload) > CHUNK_MAX:
        raise ProtocolError("chunk too large", "too_large")
    return CHUNK_HDR.pack(int(seq) & 0xFFFFFFFF, int(offset) & 0xFFFFFFFFFFFFFFFF,
                          len(payload)) + payload


class ChunkFramer(object):
    """Incremental parser for the DATA chunk stream."""

    def __init__(self, max_payload=CHUNK_MAX):
        self.max_payload = max_payload
        self._buf = bytearray()
        self.frames = 0

    @property
    def pending(self):
        return len(self._buf)

    def feed(self, data):
        if data:
            self._buf += data

    def pop(self):
        """Return ``(seq, offset, payload)`` or ``None`` when incomplete."""
        buf = self._buf
        if len(buf) < CHUNK_HDR.size:
            return None
        seq, offset, length = CHUNK_HDR.unpack(bytes(buf[:CHUNK_HDR.size]))
        if length > self.max_payload:
            raise ProtocolError("chunk payload too large (%d)" % (length,), "too_large")
        total = CHUNK_HDR.size + length
        if len(buf) < total:
            return None
        payload = bytes(buf[CHUNK_HDR.size:total])
        del buf[:total]
        self.frames += 1
        return seq, offset, payload


def ctl_preamble_line(token=None):
    """``b"CTL <token>\\n"`` -- WiFi CTL fallback preamble (PROTOCOL.md 5.3).

    The initiator writes this line before the length-prefixed JSON frames.
    There is no session token before ``hello_ack``, so ``"-"`` is sent as the
    placeholder (matching the iOS client).
    """
    value = str(token) if token else "-"
    line = ("CTL %s\n" % (value,)).encode("ascii", "ignore")
    if len(line) > DATA_LINE_MAX:
        raise ProtocolError("CTL preamble too long", "too_large")
    return line


def data_header_line(token, transfer_id):
    """``b"DATA <token> <id>\\n"``."""
    token = str(token or "")
    transfer_id = str(transfer_id or "")
    if not token or not transfer_id:
        raise ProtocolError("token/id required for the data channel", "badreq")
    line = ("%s %s %s\n" % (DATA_PREFIX.decode("ascii"), token, transfer_id)).encode("ascii")
    if len(line) > DATA_LINE_MAX:
        raise ProtocolError("data header line too long", "too_large")
    return line


def parse_data_header(line):
    """Parse ``DATA <token> <id>`` (with or without the newline)."""
    if isinstance(line, bytes):
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            raise ProtocolError("data header is not ASCII", "badreq")
    else:
        text = str(line)
    text = text.strip()
    parts = text.split()
    if len(parts) != 3 or parts[0] != DATA_PREFIX.decode("ascii"):
        raise ProtocolError("bad data header %r" % (text[:48],), "badreq")
    token, transfer_id = parts[1], parts[2]
    if len(token) < 8 or len(token) > 64 or len(transfer_id) < 4 or len(transfer_id) > 64:
        raise ProtocolError("bad token/id in data header", "badreq")
    return token, transfer_id


# ---------------------------------------------------------------------------
# CTL message builders
# ---------------------------------------------------------------------------
def _osv_text(osv, limit=64):
    text = str(osv or "windows")
    return text[:limit]


def hello_message(did, name, kind, osv, port, caps, nonce):
    return {
        "t": M_HELLO, "v": PROTO_VERSION, "did": str(did), "name": str(name)[:64],
        "kind": kind or "windows", "osv": _osv_text(osv),
        "port": int(port or 0), "caps": int(caps or 0), "nonce": str(nonce),
    }


def hello_ack_message(did, name, kind, osv, port, caps, nonce, token):
    return {
        "t": M_HELLO_ACK, "v": PROTO_VERSION, "did": str(did), "name": str(name)[:64],
        "kind": kind or "windows", "osv": _osv_text(osv),
        "port": int(port or 0), "caps": int(caps or 0), "nonce": str(nonce),
        "token": str(token),
    }


def list_message(token, path=""):
    """``{"t":"list","token":..,"path":""}`` -- browse the peer's share root."""
    return {"t": M_LIST, "token": str(token), "path": str(path)}


def list_resp_message(entries, ok=True, err=None):
    """``{"t":"list_resp","ok":true,"files":[{path,name,size,mtime,kind}]}``."""
    files = []
    for entry in entries or []:
        files.append({
            "path": str(entry.get("path", "")),
            "name": str(entry.get("name", "")),
            "size": int(entry.get("size") or 0),
            "mtime": int(entry.get("mtime") or 0),
            "kind": str(entry.get("kind") or "file"),
        })
    msg = {"t": M_LIST_RESP, "ok": bool(ok), "files": files}
    if err:
        msg["err"] = str(err)
    return msg


def stat_message(token, path):
    """``{"t":"stat","token":..,"path":"a.txt"}``."""
    return {"t": M_STAT, "token": str(token), "path": str(path)}


def stat_resp_message(path, size=0, mtime=0, digest="", ok=True, err=None):
    """``{"t":"stat_resp","ok":true,"path":..,"size":..,"mtime":..,"hash":"<256 hex>"}``."""
    msg = {"t": M_STAT_RESP, "ok": bool(ok), "path": str(path or ""),
           "size": int(size or 0), "mtime": int(mtime or 0),
           "hash": str(digest or "").lower()}
    if err:
        msg["err"] = str(err)
    return msg


def get_message(token, transfer_id, path):
    return {"t": M_GET, "token": str(token), "id": str(transfer_id),
            "path": str(path)}


def get_resp_message(transfer_id, ok, size=0, name="", err=None):
    msg = {"t": M_GET_RESP, "id": str(transfer_id), "ok": bool(ok),
           "size": int(size or 0), "name": str(name or "")}
    if err:
        msg["err"] = str(err)
    return msg


def put_offer_message(transfer_id, name, size):
    return {"t": M_PUT_OFFER, "id": str(transfer_id), "name": str(name),
            "size": int(size or 0)}


def put_resp_message(transfer_id, ok, err=None):
    msg = {"t": M_PUT_RESP, "id": str(transfer_id), "ok": bool(ok)}
    if err:
        msg["err"] = str(err)
    return msg


def hash_message(transfer_id, digest_hex, size):
    """``{"t":"hash","id":..,"hash":"<256 hex>","size":..}``.

    The digest is SHA-KE-1024 (128 bytes / 256 hex chars).  Readers also accept
    the ``sha_ke_1024``/``sha256`` aliases, see :func:`hash_digest`.
    """
    return {"t": M_HASH, "id": str(transfer_id),
            "hash": str(digest_hex).lower(), "size": int(size or 0)}


def hash_digest(msg):
    """Extract the digest from a ``hash``/``hash_resp`` message, lowercase.

    ``"hash"`` is what this client sends; ``"sha_ke_1024"`` is what the iOS
    flow diagram uses, and the older ``"sha256"`` field name is accepted for
    forward/backward tolerance.  Returns ``""`` when none is present.
    """
    for key in ("hash", "sha_ke_1024", "sha256"):
        value = msg.get(key)
        if value:
            return str(value).strip().lower()
    return ""


def hash_resp_message(transfer_id, ok, err=None):
    """``{"t":"hash_resp","id":..,"ok":bool}`` -- ``id`` is always present."""
    msg = {"t": M_HASH_RESP, "id": str(transfer_id), "ok": bool(ok)}
    if err:
        msg["err"] = str(err)
    return msg


def err_message(err="unsupported", msg=""):
    header = {"t": "err", "err": str(err)}
    if msg:
        header["msg"] = str(msg)
    return header


def bye_message():
    return {"t": M_BYE}


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------
class RateLimiter(object):
    """Token-bucket-style throttle for outgoing DATA chunks (KiB/s, 0 = off)."""

    def __init__(self, kbps=0):
        self.kbps = 0
        self._start = time.time()
        self._sent = 0
        self.set_rate(kbps)

    def set_rate(self, kbps):
        try:
            kbps = int(kbps or 0)
        except (TypeError, ValueError):
            kbps = 0
        self.kbps = max(0, kbps)
        self.reset()

    @property
    def enabled(self):
        return self.kbps > 0

    def throttle(self, nbytes):
        if self.kbps <= 0 or nbytes <= 0:
            return 0.0
        self._sent += nbytes
        target = self._sent / (self.kbps * 1024.0)
        elapsed = time.time() - self._start
        delay = target - elapsed
        if delay > 0.002:
            time.sleep(delay)
            return delay
        return 0.0

    def reset(self):
        self._start = time.time()
        self._sent = 0
