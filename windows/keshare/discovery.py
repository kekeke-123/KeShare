# -*- coding: utf-8 -*-
"""Peer discovery: UDP probing (port 47377) plus a minimal mDNS responder.

Two mechanisms run side by side; either one alone is enough to populate the
"附近设备" list:

* **UDP probe** (PROTOCOL.md section 6.2, the reliable path).  Every 5 seconds a
  ``{"wb":1,"t":"probe",...}`` datagram is broadcast to 255.255.255.255, every
  local /24 broadcast address and 127.0.0.1.  Peers answer with a unicast
  ``announce`` datagram that carries their display name, kind, port and caps.
  An entry that has not been refreshed for 15 seconds is greyed out.

* **mDNS-lite** (section 6.1).  A deliberately small PTR+SRV+TXT encoder and the
  matching parser for ``_keshare._tcp.local`` on 224.0.0.251:5353.  mDNS is
  fiddly across Windows versions and firewalls, so every socket call is guarded
  and failures only disable this half of discovery -- the UDP probe keeps
  working.  This is documented behaviour, not a silent fallback: the reason is
  written to the log.

The :class:`PeerTable` is shared with the UI (tkinter polls :meth:`snapshot`)
and never blocks a socket loop.
"""

import json
import socket
import struct
import threading
import time

from . import mdns as ksmdns

UDP_PORT = 47377
PROBE_INTERVAL = 5.0
STALE_AFTER = 15.0
MDNS_GROUP = ksmdns.MDNS_GROUP
MDNS_PORT = ksmdns.MDNS_PORT
SERVICE_TYPE = ksmdns.SERVICE_TYPE          # matches KS_SERVICE_TYPE
MDNS_INTERVAL = 30.0
MAX_DATAGRAM = 9000

DNS_A = ksmdns.DNS_A
DNS_PTR = ksmdns.DNS_PTR
DNS_TXT = ksmdns.DNS_TXT
DNS_SRV = ksmdns.DNS_SRV

# Re-exported codec entry points (the implementation lives in keshare.mdns).
build_mdns_response = ksmdns.build_response
parse_mdns_packet = ksmdns.parse_packet


# ---------------------------------------------------------------------------
# Peer table
# ---------------------------------------------------------------------------
class Peer(object):
    """One discovered device."""

    __slots__ = ("did", "name", "kind", "osv", "ip", "port", "caps", "nonce",
                 "source", "last_seen", "online")

    def __init__(self, did, name="", kind="other", osv="", ip="", port=UDP_PORT,
                 caps=0, nonce="", source="udp"):
        self.did = str(did or "")
        self.name = str(name or "") or self.did or "?"
        self.kind = str(kind or "other")
        self.osv = str(osv or "")
        self.ip = str(ip or "")
        self.port = int(port or UDP_PORT)
        self.caps = int(caps or 0)
        self.nonce = str(nonce or "")
        self.source = source
        self.last_seen = time.time()
        self.online = True

    def update_from(self, other):
        self.name = other.name or self.name
        self.kind = other.kind or self.kind
        self.osv = other.osv or self.osv
        self.ip = other.ip or self.ip
        self.port = other.port or self.port
        if other.caps:
            self.caps = other.caps
        if other.nonce:
            self.nonce = other.nonce
        self.source = other.source or self.source
        self.last_seen = time.time()
        self.online = True

    @property
    def caps_text(self):
        bits = []
        if self.caps & 0x01:
            bits.append("文件")
        if self.caps & 0x02:
            bits.append("剪贴板")
        if self.caps & 0x04:
            bits.append("接收")
        if self.caps & 0x40:
            bits.append("续传")
        return "/".join(bits) or "-"

    def to_dict(self):
        return {
            "did": self.did, "name": self.name, "kind": self.kind,
            "osv": self.osv, "ip": self.ip, "port": self.port,
            "caps": self.caps, "online": self.online,
            "last_seen": int(self.last_seen), "source": self.source,
        }


class PeerTable(object):
    """Thread safe ``did -> Peer`` map with change notification."""

    def __init__(self, on_change=None):
        self._lock = threading.Lock()
        self._peers = {}
        self._on_change = on_change
        self._last_notify = 0.0

    def set_listener(self, callback):
        self._on_change = callback

    def upsert(self, peer, notify=True):
        if not peer.did:
            return None
        with self._lock:
            existing = self._peers.get(peer.did)
            if existing is None:
                self._peers[peer.did] = peer
                changed = True
            else:
                existing.update_from(peer)
                changed = True
        if changed and notify:
            self._notify()
        return self._peers.get(peer.did)

    def get(self, did):
        with self._lock:
            return self._peers.get(did)

    def remove(self, did):
        with self._lock:
            self._peers.pop(did, None)
        self._notify()

    def clear(self):
        with self._lock:
            self._peers.clear()

    def maintain(self, now=None):
        """Mark peers offline after 15 s without an announce; returns count."""
        now = now or time.time()
        changed = 0
        with self._lock:
            for peer in self._peers.values():
                online = (now - peer.last_seen) <= STALE_AFTER
                if peer.online != online:
                    peer.online = online
                    changed += 1
        if changed:
            self._notify()
        return changed

    def snapshot(self):
        """Sorted list of peer dicts (online first, then by name)."""
        with self._lock:
            peers = list(self._peers.values())
        peers.sort(key=lambda p: (0 if p.online else 1, p.name.lower()))
        return [p.to_dict() for p in peers]

    def online_peers(self):
        return [p for p in self.snapshot() if p.get("online")]

    def _notify(self):
        now = time.time()
        with self._lock:
            if now - self._last_notify < 0.2:
                return
            self._last_notify = now
            callback = self._on_change
        if callback:
            try:
                callback()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# UDP probe / announce
# ---------------------------------------------------------------------------
def _broadcast_targets():
    targets = {"255.255.255.255", "127.0.0.1"}
    try:
        _host, _aliases, addresses = socket.gethostbyname_ex(socket.gethostname())
        for address in addresses:
            parts = address.split(".")
            if len(parts) == 4 and not address.startswith("127."):
                targets.add("%s.%s.%s.255" % (parts[0], parts[1], parts[2]))
    except OSError:
        pass
    return sorted(targets)


class UdpProbe(object):
    """Broadcasts probes and answers probes from other KeShare instances."""

    def __init__(self, config, table, log, on_peer=None):
        self.config = config
        self.table = table
        self.log = log
        self.on_peer = on_peer
        self.sock = None
        self._stop = threading.Event()
        self._thread = None
        self._last_probe = 0.0
        self.port = int(config.port)

    def start(self):
        if self._thread is not None:
            return False
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
            sock.bind(("", self.port))
            sock.settimeout(0.5)
        except OSError as exc:
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass
            self.log.warn("UDP 探测不可用: %s" % (exc,))
            return False
        self.sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ks-udp")
        self._thread.daemon = True
        self._thread.start()
        self.log.info("UDP 探测已启动，端口 %d" % (self.port,))
        return True

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            try:
                thread.join(1.0)
            except RuntimeError:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # -- payloads ----------------------------------------------------------
    def probe_payload(self):
        return {
            "ks": 1, "t": "probe", "did": self.config.device_id,
            "name": self.config.device_name, "port": int(self.config.port),
            "caps": 0x7F,
        }

    def announce_payload(self, nonce=""):
        return {
            "ks": 1, "t": "announce", "did": self.config.device_id,
            "name": self.config.device_name, "kind": "windows",
            "osv": "windows", "port": int(self.config.port), "caps": 0x7F,
            "nonce": nonce or "%08x" % (int(time.time() * 1000) & 0xFFFFFFFF),
        }

    def _send(self, payload, target, port=None):
        """Send a probe/announce datagram.

        *target* may be a plain host (sent to our own port, used for the
        broadcast probes) or a ``(host, port)`` tuple (used to answer a probe
        back to its source, which may not be port 47377).
        """
        if isinstance(target, (tuple, list)) and len(target) == 2:
            host, dest_port = target[0], int(target[1])
        else:
            host, dest_port = target, int(port or self.port)
        try:
            data = json.dumps(payload, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            if len(data) > 512:
                # The probe datagram is capped at 512 bytes by the protocol:
                # shorten the display name rather than emit broken JSON.
                short = dict(payload)
                short["name"] = str(short.get("name", ""))[:24]
                data = json.dumps(short, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")
            if len(data) > 512:
                return False
            self.sock.sendto(data, (host, dest_port))
            return True
        except (OSError, TypeError, ValueError):
            return False

    def probe_now(self):
        if self.sock is None:
            return 0
        payload = self.probe_payload()
        sent = 0
        for target in _broadcast_targets():
            if self._send(payload, target):
                sent += 1
        self._last_probe = time.time()
        return sent

    # -- loop --------------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            now = time.time()
            if now - self._last_probe >= PROBE_INTERVAL:
                self.probe_now()
                self.table.maintain(now)
            try:
                data, addr = self.sock.recvfrom(MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_datagram(data, addr)
            except Exception as exc:                        # pragma: no cover - defensive
                self.log.debug("UDP 数据包处理失败: %s" % (exc,))

    def _handle_datagram(self, data, addr):
        if not data or len(data) > 4096:
            return
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        # 兼容两套标记：ks 是统一后的字段，wb 是旧版 Windows 字段
        if payload.get("ks") != 1 and payload.get("wb") != 1:
            return
        did = str(payload.get("did") or "")
        if not did or did == self.config.device_id:
            return
        kind = payload.get("t")
        if kind == "probe":
            self._send(self.announce_payload(), addr)
            self.log.debug("收到探测 %s" % (addr[0],))
            return
        if kind != "announce":
            return
        peer = Peer(
            did=did,
            name=payload.get("name") or did,
            kind=payload.get("kind") or "other",
            osv=payload.get("osv") or "",
            ip=addr[0],
            port=int(payload.get("port") or UDP_PORT),
            caps=int(payload.get("caps") or 0),
            nonce=payload.get("nonce") or "",
            source="udp",
        )
        known = self.table.get(did)
        if known is None:
            self.log.info("发现设备 %s (%s)" % (peer.name, peer.ip))
        elif known.nonce == peer.nonce and known.ip == peer.ip:
            # Same announce seen again: only refresh the timestamp.
            known.last_seen = time.time()
            known.online = True
            return
        self.table.upsert(peer)
        if self.on_peer is not None:
            try:
                self.on_peer(peer)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# mDNS-lite codec lives in keshare.mdns; the names below are re-exported
# so callers can keep using discovery.build_mdns_response(...).
# ---------------------------------------------------------------------------
class MdnsLite(object):
    """Minimal mDNS advertiser/parser.  May silently disable itself."""

    def __init__(self, config, table, log):
        self.config = config
        self.table = table
        self.log = log
        self.sock = None
        self._thread = None
        self._stop = threading.Event()
        self._last_announce = 0.0
        self.enabled = False

    def _instance(self):
        name = "".join(ch for ch in self.config.device_name if ch not in ".")
        return "%s-%s" % (name[:32] or "KeShare", self.config.device_id[-4:].lower())

    def _host(self):
        return "keshare-%s.local" % (self.config.device_id.lower(),)

    def start(self):
        if self._thread is not None:
            return False
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            except OSError:
                pass
            sock.bind(("", MDNS_PORT))
            try:
                membership = socket.inet_aton(MDNS_GROUP) + socket.inet_aton("0.0.0.0")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            except OSError as exc:
                self.log.debug("mDNS 组播加入失败（仅监听）: %s" % (exc,))
            sock.settimeout(0.5)
        except OSError as exc:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self.log.warn("mDNS 不可用，改用 UDP 探测: %s" % (exc,))
            self.enabled = False
            return False
        self.sock = sock
        self.enabled = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ks-mdns")
        self._thread.daemon = True
        self._thread.start()
        self.log.info("mDNS 已启动: %s" % (SERVICE_TYPE,))
        return True

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            try:
                thread.join(1.0)
            except RuntimeError:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _packet(self):
        txt = [
            ("v", 1),
            ("did", self.config.device_id),
            ("name", self.config.device_name),
            ("kind", "windows"),
            ("caps", 0x7F),
            ("port", int(self.config.port)),
        ]
        return build_mdns_response(self._instance(), self._host(),
                                   int(self.config.port), txt)

    def announce(self):
        if not self.sock:
            return False
        try:
            self.sock.sendto(self._packet(), (MDNS_GROUP, MDNS_PORT))
            self._last_announce = time.time()
            return True
        except OSError:
            return False

    def _loop(self):
        while not self._stop.is_set():
            if time.time() - self._last_announce >= MDNS_INTERVAL:
                self.announce()
            try:
                data, addr = self.sock.recvfrom(MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle(data, addr)
            except Exception as exc:                        # pragma: no cover - defensive
                self.log.debug("mDNS 数据包处理失败: %s" % (exc,))
                continue

    def _handle(self, data, addr):
        questions, records = parse_mdns_packet(data)
        if not records and not questions:
            return
        # Someone is asking for our service: answer.
        for question in questions:
            if question["name"].rstrip(".").lower() == SERVICE_TYPE and question["type"] in (DNS_PTR, 255):
                self.announce()
                break
        txt = {}
        ports = {}
        instances = set()
        for record in records:
            name = record["name"].rstrip(".").lower()
            if record["type"] == DNS_PTR and name == SERVICE_TYPE:
                target = record.get("target", "")
                if target:
                    instances.add(target.rstrip("."))
            elif record["type"] == DNS_SRV:
                ports[name] = record.get("port") or 0
            elif record["type"] == DNS_TXT:
                values = {}
                for item in record.get("txt", []):
                    if "=" in item:
                        key, value = item.split("=", 1)
                        values[key.strip()] = value.strip()
                txt[name] = values
        did = ""
        for key, values in txt.items():
            if values.get("did"):
                did = values["did"]
                instance_key = key
                break
        else:
            instance_key = ""
        if not did or did == self.config.device_id:
            return
        values = txt.get(instance_key, {})
        port = 0
        if instance_key in ports:
            port = ports[instance_key]
        if not port:
            try:
                port = int(values.get("port") or UDP_PORT)
            except ValueError:
                port = UDP_PORT
        peer = Peer(
            did=did,
            name=values.get("name") or did,
            kind=values.get("kind") or "other",
            osv=values.get("osv") or "",
            ip=addr[0],
            port=port,
            caps=int(values.get("caps") or 0),
            source="mdns",
        )
        if self.table.upsert(peer) is not None:
            self.log.info("mDNS 发现设备 %s (%s)" % (peer.name, peer.ip))


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------
class DiscoveryService(object):
    """Owns the probe/mDNS workers according to ``network.discovery``."""

    def __init__(self, config, log, on_change=None):
        self.config = config
        self.log = log
        self.table = PeerTable(on_change=on_change)
        self.udp = UdpProbe(config, self.table, log)
        self.mdns = MdnsLite(config, self.table, log)
        self.started = False

    def start(self):
        if self.started:
            return
        mode = self.config.discovery_mode
        self.started = True
        if mode in ("both", "udp"):
            self.udp.start()
        else:
            self.log.info("UDP 探测未启用 (discovery=%s)" % (mode,))
        if mode in ("both", "mdns"):
            if not self.mdns.start():
                self.log.info("mDNS 不可用 -> 依赖 UDP 探测")
        else:
            self.log.info("mDNS 未启用 (discovery=%s)" % (mode,))

    def stop(self):
        if not self.started:
            return
        self.started = False
        try:
            self.udp.stop()
        except Exception:
            pass
        try:
            self.mdns.stop()
        except Exception:
            pass

    def restart(self):
        self.stop()
        self.udp = UdpProbe(self.config, self.table, self.log)
        self.mdns = MdnsLite(self.config, self.table, self.log)
        self.start()

    def probe_now(self):
        try:
            return self.udp.probe_now()
        except Exception:
            return 0

    def snapshot(self):
        return self.table.snapshot()
