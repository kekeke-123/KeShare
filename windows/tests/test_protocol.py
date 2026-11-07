# -*- coding: utf-8 -*-
"""Unit tests for the non-UI KeShare modules (stdlib unittest only).

Run with::

    python -m unittest discover -s D:\\KeShare\\windows\\tests -v

The tkinter UI is deliberately not imported here (tests stay headless).
"""

import hashlib
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import unittest

WINDOWS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WINDOWS_ROOT not in sys.path:
    sys.path.insert(0, WINDOWS_ROOT)

from keshare import config as ksconfig                      # noqa: E402
from keshare import crypto, data, discovery, files, ke1024  # noqa: E402
from keshare import ctl, protocol                            # noqa: E402
from keshare.log import Log                                 # noqa: E402

SCRATCH = os.path.join(os.path.dirname(WINDOWS_ROOT), ".scratch", "tmp")


def temp_dir(prefix="ks-test-"):
    """mkdtemp with fallbacks (some sandboxes block the default %TEMP%)."""
    candidates = [tempfile.gettempdir(), os.environ.get("TMP"),
                  os.environ.get("TEMP"), SCRATCH]
    seen = []
    for base in candidates:
        if not base or base in seen:
            continue
        seen.append(base)
        try:
            if not os.path.isdir(base):
                os.makedirs(base)
            return tempfile.mkdtemp(prefix=prefix, dir=base)
        except OSError:
            continue
    raise RuntimeError("no writable temporary directory (%r)" % (seen,))


class TempMixin(object):
    def make_dir(self):
        path = temp_dir()
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def make_log(self, folder):
        return Log(path=os.path.join(folder, "test.log"), ring_size=2000, verbose=True)


# ---------------------------------------------------------------------------
# SHA-KE-1024
# ---------------------------------------------------------------------------
class Ke1024Tests(unittest.TestCase):
    def test_published_vectors(self):
        for payload, expected in ke1024.VECTORS:
            self.assertEqual(len(expected), 64)
            self.assertEqual(len(bytes.fromhex(expected)), 32)
            self.assertEqual(ke1024.sha_ke_1024_hex(payload), expected,
                             "vector failed for %d bytes" % len(payload))
            self.assertEqual(ke1024.sha_ke_1024(payload), bytes.fromhex(expected))

    def test_streaming_equals_one_shot(self):
        payload = bytes(bytearray((i * 37 + 11) & 0xFF for i in range(4096)))
        expected = ke1024.sha_ke_1024_hex(payload)
        for step in (1, 127, 128, 129, 1000):
            hasher = ke1024.Ke1024()
            position = 0
            while position < len(payload):
                hasher.update(payload[position:position + step])
                position += step
            self.assertEqual(hasher.hexdigest(), expected,
                             "streaming mismatch at chunk %d" % step)
            self.assertEqual(hasher.digest(), bytes.fromhex(expected))

    def test_file_helper_matches_memory(self):
        folder = temp_dir()
        self.addCleanup(shutil.rmtree, folder, True)
        payload = os.urandom(300 * 1024)
        path = os.path.join(folder, "blob.bin")
        with open(path, "wb") as handle:
            handle.write(payload)
        self.assertEqual(ke1024.sha_ke_1024_file(path, chunk=4096),
                         ke1024.sha_ke_1024_hex(payload))

    def test_crypto_reexport(self):
        self.assertIs(crypto.sha_ke_1024_hex, ke1024.sha_ke_1024_hex)
        self.assertEqual(crypto.sha_ke_1024_hex(b"abc"),
                         ke1024.sha_ke_1024_hex(b"abc"))


# ---------------------------------------------------------------------------
# CTL framing
# ---------------------------------------------------------------------------
class CtlFrameTests(unittest.TestCase):
    def test_round_trip(self):
        message = protocol.hello_message("1111111111111111", "客厅电脑", "windows",
                                         "10.0.19045", 47377, protocol.CAPS_DEFAULT,
                                         "0011223344556677")
        raw = protocol.encode_ctl(message)
        self.assertEqual(struct.unpack(">I", raw[:4])[0], len(raw) - 4)

        framer = protocol.CtlFramer()
        framer.feed(raw)
        decoded = framer.pop()
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["t"], "hello")
        self.assertEqual(decoded["name"], "客厅电脑")
        self.assertEqual(decoded["port"], 47377)
        self.assertEqual(decoded["v"], 1)
        self.assertIsNone(framer.pop())
        self.assertEqual(framer.pending, 0)

    def test_split_reads(self):
        messages = [protocol.bye_message(),
                    protocol.put_offer_message("a1b2c3d4", "报告.pdf", 2048),
                    protocol.hash_message("a1b2c3d4", "ab" * 128, 2048)]
        raw = b"".join(protocol.encode_ctl(m) for m in messages)

        framer = protocol.CtlFramer()
        decoded = []
        for index in range(len(raw)):
            framer.feed(raw[index:index + 1])
            while True:
                message = framer.pop()
                if message is None:
                    break
                decoded.append(message)
        self.assertEqual([m["t"] for m in decoded], ["bye", "put_offer", "hash"])
        self.assertEqual(decoded[1]["name"], "报告.pdf")
        self.assertEqual(decoded[2]["hash"], "ab" * 128)

    def test_coalesced_reads(self):
        raw = b"".join(protocol.encode_ctl(protocol.get_message("t" * 16, "deadbeef",
                                                               "a/b.txt"))
                       for _ in range(3))
        framer = protocol.CtlFramer()
        framer.feed(raw)
        seen = []
        while True:
            message = framer.pop()
            if message is None:
                break
            seen.append(message)
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0]["path"], "a/b.txt")

    def test_send_limit_and_read_limit(self):
        # PROTOCOL.md 1.1: a CTL frame is at most 4096 bytes on the wire.
        big = {"t": "hash", "id": "abcd1234", "hash": "ab" * 128,
               "pad": "x" * 5000}
        with self.assertRaises(protocol.ProtocolError):
            protocol.encode_ctl(big)
        self.assertEqual(protocol.CTL_MAX_JSON, 4096)

        # A receiver tolerates up to CTL_MAX_READ and rejects anything larger.
        framer = protocol.CtlFramer()
        framer.feed(struct.pack(">I", protocol.CTL_MAX_READ + 1))
        with self.assertRaises(protocol.ProtocolError):
            framer.pop()

    def test_bad_json_and_envelope(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.json_loads(b"not json")
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_message({"no": "type"})
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_message({"t": "hello", "v": 2})
        self.assertEqual(protocol.validate_message({"t": "bye"})["t"], "bye")

    def test_required_fields(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.require_fields({"t": "get", "id": ""}, "id", "path")
        self.assertTrue(protocol.require_fields({"id": "x", "path": "y"},
                                                "id", "path"))

    def test_data_header_line(self):
        line = protocol.data_header_line("0123456789abcdef", "deadbeef")
        self.assertEqual(line, b"DATA 0123456789abcdef deadbeef\n")
        token, transfer_id = protocol.parse_data_header(line)
        self.assertEqual(token, "0123456789abcdef")
        self.assertEqual(transfer_id, "deadbeef")
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_data_header(b"DATA onlytoken\n")
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_data_header(b"GET token id\n")

    def test_hash_message_shape(self):
        msg = protocol.hash_message("abcd1234", "AB" * 128, 99)
        self.assertEqual(msg["t"], "hash")
        self.assertEqual(msg["id"], "abcd1234")
        self.assertEqual(msg["hash"], "ab" * 128)          # lowercased
        self.assertEqual(msg["size"], 99)
        self.assertNotIn("sha256", msg)

        # readers accept the documented aliases
        self.assertEqual(protocol.hash_digest({"hash": "AA" * 128}), "aa" * 128)
        self.assertEqual(protocol.hash_digest({"sha_ke_1024": "BB" * 128}), "bb" * 128)
        self.assertEqual(protocol.hash_digest({"sha256": "CC" * 128}), "cc" * 128)
        self.assertEqual(protocol.hash_digest({}), "")

    def test_hash_resp_always_has_id(self):
        for ok in (True, False):
            msg = protocol.hash_resp_message("deadbeef", ok,
                                             None if ok else "checksum")
            self.assertEqual(msg["t"], "hash_resp")
            self.assertEqual(msg["id"], "deadbeef")
            self.assertEqual(msg["ok"], ok)
        self.assertEqual(protocol.hash_resp_message("x", False, "checksum")["err"],
                         "checksum")


    def test_list_stat_message_shapes(self):
        listing = protocol.list_resp_message([
            {"path": "a.txt", "name": "a.txt", "size": 123,
             "mtime": 1700000000, "kind": "file"}])
        self.assertEqual(listing["t"], "list_resp")
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["files"][0]["size"], 123)
        self.assertEqual(sorted(listing["files"][0].keys()),
                         ["kind", "mtime", "name", "path", "size"])
        self.assertEqual(protocol.list_resp_message([], False, "denied")["err"],
                         "denied")

        info = protocol.stat_resp_message("a.txt", 123, 1700000000, "AB" * 128)
        self.assertEqual(info["t"], "stat_resp")
        self.assertEqual(info["hash"], "ab" * 128)
        self.assertEqual(info["size"], 123)
        self.assertEqual(protocol.stat_resp_message("x", ok=False,
                                                    err="notfound")["err"],
                         "notfound")

    def test_ctl_preamble_line(self):
        self.assertEqual(protocol.ctl_preamble_line(), b"CTL -\n")
        self.assertEqual(protocol.ctl_preamble_line("0123456789abcdef"),
                         b"CTL 0123456789abcdef\n")


# ---------------------------------------------------------------------------
# CTL transports
# ---------------------------------------------------------------------------
class CtlTransportTests(unittest.TestCase):
    def test_connect_wifi_sends_preamble(self):
        """The WiFi CTL initiator writes ``CTL <token>\\n`` first (5.3)."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        captured = []

        def reader():
            try:
                conn, _addr = listener.accept()
                buffer = bytearray()
                while b"\n" not in buffer:
                    block = conn.recv(1)
                    if not block:
                        break
                    buffer += block
                captured.append(bytes(buffer))
                conn.close()
            finally:
                listener.close()

        thread = threading.Thread(target=reader)
        thread.daemon = True
        thread.start()
        link = ctl.connect_wifi("127.0.0.1", port, 5.0,
                                token="0123456789abcdef")
        link.close()
        thread.join(5)
        self.assertEqual(captured, [b"CTL 0123456789abcdef\n"])


# ---------------------------------------------------------------------------
# Token derivation (SHA-64, PROTOCOL.md section 2)
# ---------------------------------------------------------------------------
class TokenTests(unittest.TestCase):
    def test_known_value(self):
        did_a, nonce_a = "1111111111111111", "0011223344556677"
        did_b, nonce_b = "2222222222222222", "8899aabbccddeeff"
        expected = hashlib.sha256(
            b"KS1-CTL\0" + did_a.encode() + nonce_a.encode()
            + did_b.encode() + nonce_b.encode()).hexdigest()[:16]
        token = crypto.token_for(did_a, nonce_a, did_b, nonce_b)
        self.assertEqual(token, expected)
        self.assertEqual(len(token), 16)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in token))

    def test_role_order_matters(self):
        forward = crypto.token_for("A" * 16, "1" * 16, "B" * 16, "2" * 16)
        backward = crypto.token_for("B" * 16, "2" * 16, "A" * 16, "1" * 16)
        self.assertNotEqual(forward, backward)

    def test_accepts_non_ascii_gracefully(self):
        token = crypto.token_for("设备甲", "nonce", "设备乙", "nonce2")
        self.assertEqual(len(token), 16)


# ---------------------------------------------------------------------------
# Chunk framing + streaming
# ---------------------------------------------------------------------------
class ChunkTests(unittest.TestCase):
    def test_chunk_round_trip(self):
        payload = bytes(bytearray(range(64))) * 4
        raw = protocol.encode_chunk(7, 4096, payload)
        framer = protocol.ChunkFramer()
        framer.feed(raw)
        seq, offset, decoded = framer.pop()
        self.assertEqual((seq, offset), (7, 4096))
        self.assertEqual(decoded, payload)
        self.assertIsNone(framer.pop())

    def test_chunk_split_reads(self):
        frames = [protocol.encode_chunk(index, index * 11, bytes([index]) * 5)
                  for index in range(4)]
        raw = b"".join(frames)
        framer = protocol.ChunkFramer()
        decoded = []
        for index in range(len(raw)):
            framer.feed(raw[index:index + 1])
            while True:
                frame = framer.pop()
                if frame is None:
                    break
                decoded.append(frame)
        self.assertEqual([f[0] for f in decoded], [0, 1, 2, 3])

    def test_chunk_oversize_rejected(self):
        header = struct.pack(">IQI", 0, 0, protocol.CHUNK_MAX + 1)
        framer = protocol.ChunkFramer()
        framer.feed(header)
        with self.assertRaises(protocol.ProtocolError):
            framer.pop()

    def test_stream_reassembly_over_loopback_tcp(self):
        """300 KB file through data.send_file -> TCP -> data.recv_file."""
        folder = temp_dir()
        self.addCleanup(shutil.rmtree, folder, True)
        payload = os.urandom(300 * 1024)
        source = os.path.join(folder, "source.bin")
        with open(source, "wb") as handle:
            handle.write(payload)
        expected = ke1024.sha_ke_1024_hex(payload)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        results = []
        errors = []

        def receiver():
            try:
                conn, _addr = listener.accept()
                conn.settimeout(20)
                with open(os.path.join(folder, "received.bin"), "wb") as out:
                    results.append(data.recv_file(conn, out,
                                                  expected_size=len(payload),
                                                  timeout=60))
                conn.close()
            except Exception as exc:                       # pragma: no cover
                errors.append(repr(exc))
            finally:
                listener.close()

        thread = threading.Thread(target=receiver)
        thread.daemon = True
        thread.start()
        sock = socket.create_connection(("127.0.0.1", port), 10)
        with open(source, "rb") as handle:
            sent = data.send_file(sock, handle, len(payload), chunk=4096)
        sock.close()
        thread.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(sent["sent"], len(payload))
        self.assertEqual(sent["digest"], expected)
        self.assertEqual(results[0]["size"], len(payload))
        self.assertEqual(results[0]["digest"], expected)
        with open(os.path.join(folder, "received.bin"), "rb") as handle:
            self.assertEqual(handle.read(), payload)

    def test_verify_hash_detects_corruption(self):
        folder = temp_dir()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "file.bin")
        with open(path, "wb") as handle:
            handle.write(b"hello keshare")
        digest = ke1024.sha_ke_1024_file(path)
        self.assertEqual(len(digest), 64)
        self.assertEqual(data.verify_hash(path, digest), digest)

        with open(path, "r+b") as handle:                  # flip one byte
            handle.seek(2)
            original = handle.read(1)
            handle.seek(2)
            handle.write(bytes([original[0] ^ 0xFF]))
        with self.assertRaises(data.DataError) as caught:
            data.verify_hash(path, digest)
        self.assertEqual(caught.exception.err, "checksum")
        self.assertFalse(os.path.exists(path), "corrupt file must be deleted")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
class ConfigTests(TempMixin, unittest.TestCase):
    def test_defaults_are_complete(self):
        folder = self.make_dir()
        path = os.path.join(folder, "settings.json")
        cfg = ksconfig.Config.load(path)
        for section in ("version", "device", "share", "network", "ui", "developer"):
            self.assertIn(section, cfg.data)
        self.assertEqual(cfg.data["version"], 1)
        self.assertEqual(len(cfg.device_id), 16)
        self.assertEqual(len(cfg.secret_hex), 64)
        self.assertEqual(cfg.port, 47377)
        self.assertEqual(cfg.data["share"]["chunk_kb"], 64)
        self.assertEqual(cfg.data["ui"]["theme"], "dark")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8")
        parsed = json.loads(text)
        self.assertEqual(list(parsed.keys()), sorted(parsed.keys()))
        self.assertIn('\n  "developer"', text)

    def test_roundtrip_preserves_identity_and_unknown_keys(self):
        folder = self.make_dir()
        path = os.path.join(folder, "settings.json")
        cfg = ksconfig.Config.load(path)
        first_id = cfg.device_id
        first_secret = cfg.secret_hex
        cfg.set("device.name", "客厅电脑")
        cfg.set("share.max_speed_kbps", 512)
        cfg.set("custom.extra", {"keep": [1, 2, 3]})
        cfg.save()

        again = ksconfig.Config.load(path)
        self.assertEqual(again.device_id, first_id)
        self.assertEqual(again.secret_hex, first_secret)
        self.assertEqual(again.device_name, "客厅电脑")
        self.assertEqual(again.max_speed_kbps, 512)
        self.assertEqual(again.get("custom.extra"), {"keep": [1, 2, 3]})

    def test_missing_keys_filled_and_clamped(self):
        folder = self.make_dir()
        path = os.path.join(folder, "settings.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"device": {"name": "只有名字"},
                       "network": {"port": 99999},
                       "share": {"max_speed_kbps": -5}}, handle)
        cfg = ksconfig.Config.load(path)
        self.assertEqual(cfg.device_name, "只有名字")
        self.assertEqual(cfg.port, 65535)
        self.assertEqual(cfg.max_speed_kbps, 0)
        self.assertEqual(cfg.data["share"]["chunk_kb"], 64)
        self.assertIn("developer", cfg.data)

    def test_typed_accessors(self):
        folder = self.make_dir()
        cfg = ksconfig.Config({
            "device": {"name": "PC", "id": "ABCDEF0123456789", "secret": "aa" * 32},
            "share": {"root": folder, "receive_dir": "Received", "chunk_kb": 32,
                      "max_speed_kbps": 128, "max_file_mb": 4},
            "network": {"port": 5000},
        }, os.path.join(folder, "settings.json"))
        self.assertEqual(cfg.device_id, "ABCDEF0123456789")
        self.assertEqual(cfg.chunk_bytes, 32 * 1024)
        self.assertEqual(cfg.max_file_bytes, 4 * 1024 * 1024)
        self.assertEqual(cfg.receive_dir, os.path.normpath(
            os.path.join(folder, "Received")))
        self.assertEqual(cfg.port, 5000)


# ---------------------------------------------------------------------------
# Path sandboxing
# ---------------------------------------------------------------------------
class SandboxTests(TempMixin, unittest.TestCase):
    def test_rejects_dangerous_paths(self):
        bad = [
            "../etc/passwd", "..\\windows\\system32", "a/../../b", "..",
            "/etc/passwd", "\\windows\\win.ini", "C:/Windows/notepad.exe",
            "C:\\Windows\\notepad.exe", "//server/share/file",
            "CON", "con.txt", "NUL", "nul.log", "AUX", "PRN", "COM1", "LPT9",
            "COM1.txt", "a/CON/b", "foo\x00bar", "a\x01b", "bad<name", "a:b",
            "trailing.", "trailing ", "x" * 1100,
        ]
        for path in bad:
            with self.assertRaises(files.SandboxError, msg="should reject %r" % (path,)):
                files.normalise_rel(path)
            self.assertFalse(files.is_safe_rel(path), "is_safe_rel(%r)" % (path,))

    def test_accepts_normal_paths(self):
        self.assertEqual(files.normalise_rel(""), "")
        self.assertEqual(files.normalise_rel("notes.txt"), "notes.txt")
        self.assertEqual(files.normalise_rel("Photos/2024/a b.jpg"),
                         "Photos/2024/a b.jpg")
        self.assertEqual(files.normalise_rel("dir\\sub\\file.txt"),
                         "dir/sub/file.txt")
        self.assertEqual(files.normalise_rel("相册/照片.jpg"), "相册/照片.jpg")

    def test_share_root_resolve_stays_inside(self):
        folder = self.make_dir()
        share = files.ShareRoot(folder)
        self.assertEqual(os.path.normcase(share.resolve("a/b.txt")),
                         os.path.normcase(os.path.join(folder, "a", "b.txt")))
        with self.assertRaises(files.SandboxError):
            share.resolve("../evil.txt")
        with self.assertRaises(files.SandboxError):
            share.resolve("C:/Windows/System32/drivers/etc/hosts")

    def test_list_stat_and_receive_target(self):
        folder = self.make_dir()
        share = files.ShareRoot(folder)
        os.makedirs(os.path.join(folder, "sub"))
        with open(os.path.join(folder, "a.txt"), "wb") as handle:
            handle.write(b"hello")
        listing = share.list_dir("", 200, 0)
        self.assertEqual(listing["total"], 2)
        self.assertEqual(listing["files"][0]["kind"], "dir")
        self.assertEqual(listing["files"][1]["name"], "a.txt")
        self.assertEqual(listing["files"][1]["size"], 5)
        info = share.stat("a.txt")
        self.assertEqual(info["hash"], ke1024.sha_ke_1024_hex(b"hello"))
        target = share.receive_target("", "a.txt")
        self.assertTrue(target.endswith("a (1).txt"))
        self.assertEqual(share.rel_of(target), "a (1).txt")


# ---------------------------------------------------------------------------
# Discovery (UDP announce parsing + mDNS encoding)
# ---------------------------------------------------------------------------
class DiscoveryTests(TempMixin, unittest.TestCase):
    def _config(self, folder):
        return ksconfig.Config({
            "device": {"name": "发现测试", "id": "DDDDDDDDDDDDDDDD", "secret": "dd" * 32},
            "share": {"root": folder, "receive_dir": "Received"},
            "network": {"port": 0, "discovery": "off"},
        }, os.path.join(folder, "settings.json"))

    def test_announce_is_added_to_the_peer_table(self):
        folder = self.make_dir()
        cfg = self._config(folder)
        log = self.make_log(folder)
        table = discovery.PeerTable()
        probe = discovery.UdpProbe(cfg, table, log)
        payload = json.dumps({
            "wb": 1, "t": "announce", "did": "EEEEEEEEEEEEEEEE", "name": "手机",
            "kind": "ios", "osv": "6.1.6", "port": 47377, "caps": 163,
            "nonce": "0011223344556677",
        }).encode("utf-8")
        probe._handle_datagram(payload, ("192.168.1.9", 5000))
        peers = table.snapshot()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["did"], "EEEEEEEEEEEEEEEE")
        self.assertEqual(peers[0]["name"], "手机")
        self.assertEqual(peers[0]["ip"], "192.168.1.9")
        self.assertEqual(peers[0]["port"], 47377)

        # our own announce must be ignored
        own = json.dumps({"wb": 1, "t": "announce", "did": cfg.device_id,
                          "name": "self", "port": 1}).encode("utf-8")
        probe._handle_datagram(own, ("192.168.1.10", 5000))
        self.assertEqual(len(table.snapshot()), 1)

    def test_mdns_record_round_trip(self):
        packet = discovery.build_mdns_response(
            "PC-1234", "keshare-aaaa.local", 47377,
            [("v", 1), ("did", "AAAAAAAAAAAAAAAA"), ("name", "PC"),
             ("kind", "windows"), ("caps", 127), ("port", 47377)])
        questions, records = discovery.parse_mdns_packet(packet)
        self.assertEqual(questions, [])
        ptr = [r for r in records if r["type"] == discovery.DNS_PTR]
        srv = [r for r in records if r["type"] == discovery.DNS_SRV]
        txt = [r for r in records if r["type"] == discovery.DNS_TXT]
        self.assertEqual(len(ptr), 1)
        self.assertEqual(ptr[0]["name"], "_keshare._tcp.local")
        self.assertIn("PC-1234._keshare._tcp.local", ptr[0]["target"])
        self.assertEqual(len(srv), 1)
        self.assertEqual(srv[0]["port"], 47377)
        self.assertEqual(len(txt), 1)
        self.assertIn("did=AAAAAAAAAAAAAAAA", txt[0]["txt"])
        self.assertIn("caps=127", txt[0]["txt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
