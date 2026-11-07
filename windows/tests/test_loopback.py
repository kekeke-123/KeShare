# -*- coding: utf-8 -*-
"""Full loopback transfer tests (server + client in threads, PROTOCOL.md 4.0).

``get``: the requester opens the DATA connection; ``put``: the receiver does.
Both directions are exercised with a 300 KB file and SHA-KE-1024 checks.
"""

import io
import json
import os
import shutil
import socket
import struct
import tempfile
import unittest
from unittest import mock

import sys

WINDOWS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WINDOWS_ROOT not in sys.path:
    sys.path.insert(0, WINDOWS_ROOT)

from keshare import config as ksconfig
from keshare import crypto, data, ke1024, protocol
from keshare.client import KSClient, KSClientError
from keshare.log import Log
from keshare.server import KSServer


def temp_dir(prefix="ks-loopback-"):
    candidates = [tempfile.gettempdir(), os.environ.get("TMP"),
                  os.environ.get("TEMP"),
                  os.path.join(os.path.dirname(WINDOWS_ROOT), ".scratch", "tmp")]
    for base in candidates:
        if not base:
            continue
        try:
            if not os.path.isdir(base):
                os.makedirs(base)
            return tempfile.mkdtemp(prefix=prefix, dir=base)
        except OSError:
            continue
    raise RuntimeError("no writable temporary directory")


class TempMixin(object):
    def make_dir(self):
        path = temp_dir()
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def make_log(self, folder):
        return Log(path=os.path.join(folder, "test.log"), ring_size=2000,
                   verbose=True)


class LoopbackTests(TempMixin, unittest.TestCase):
    def _setup(self):
        folder = self.make_dir()
        share_a = os.path.join(folder, "share-a")
        share_b = os.path.join(folder, "share-b")
        os.makedirs(share_a)
        os.makedirs(share_b)
        payload = os.urandom(300 * 1024)
        with open(os.path.join(share_a, "big.bin"), "wb") as handle:
            handle.write(payload)

        cfg_a = ksconfig.Config({
            "device": {"name": "A端", "id": "AAAAAAAAAAAAAAAA", "secret": "aa" * 32},
            "share": {"root": share_a, "receive_dir": "Received", "chunk_kb": 64},
            "network": {"port": 0},
        }, os.path.join(folder, "a.json"))
        cfg_b = ksconfig.Config({
            "device": {"name": "B端", "id": "BBBBBBBBBBBBBBBB", "secret": "bb" * 32},
            "share": {"root": share_b, "receive_dir": "Received", "chunk_kb": 64},
            "network": {"port": 0},
        }, os.path.join(folder, "b.json"))
        return folder, share_a, share_b, payload, cfg_a, cfg_b

    def _start(self, folder, cfg_a, cfg_b):
        log = self.make_log(folder)
        server_a = KSServer(cfg_a, log)
        server_b = KSServer(cfg_b, log)
        port_a = server_a.start()
        port_b = server_b.start()
        client = KSClient(cfg_b, "127.0.0.1", port_a, log=log,
                          local_port=port_b, registry=server_b.registry)
        return log, server_a, server_b, port_a, port_b, client

    def test_get_push_and_hash(self):
        folder, share_a, share_b, payload, cfg_a, cfg_b = self._setup()
        log, server_a, server_b, port_a, port_b, client = self._start(
            folder, cfg_a, cfg_b)
        try:
            self.assertNotEqual(port_a, port_b)
            session = client.connect()
            self.assertEqual(session.did, "AAAAAAAAAAAAAAAA")
            self.assertEqual(len(session.token), 16)
            self.assertIn(session.transport_label, ("WiFi 回退", "蓝牙"))

            # ---- download: B is the receiver, so B connects to A ---------
            seen = []
            result = client.get("big.bin", os.path.join(share_b, "downloaded.bin"),
                                progress=lambda sent, total: seen.append((sent, total)))
            self.assertEqual(result["size"], len(payload))
            self.assertEqual(len(result["hash"]), 256)
            self.assertEqual(result["hash"], ke1024.sha_ke_1024_hex(payload))
            self.assertTrue(seen, "progress callback must be called")
            with open(os.path.join(share_b, "downloaded.bin"), "rb") as handle:
                self.assertEqual(handle.read(), payload)
            self.assertFalse(os.path.exists(
                os.path.join(share_b, "downloaded.bin.part")))

            # ---- download into the default receive directory ------------
            default = client.get("big.bin")
            self.assertTrue(os.path.dirname(default["dest"]).endswith("Received"))

            # ---- empty file and non-ASCII file name ---------------------
            empty_src = os.path.join(share_a, "empty.bin")
            with open(empty_src, "wb"):
                pass
            empty = client.get("empty.bin")
            self.assertEqual(empty["size"], 0)
            self.assertEqual(empty["hash"], ke1024.sha_ke_1024_hex(b""))

            cn_text = "你好 KeShare"
            cn_src = os.path.join(share_a, "中文 文件.txt")
            with open(cn_src, "wb") as handle:
                handle.write(cn_text.encode("utf-8"))
            cn = client.get("中文 文件.txt")
            with open(cn["dest"], "rb") as handle:
                self.assertEqual(handle.read(), cn_text.encode("utf-8"))

            # ---- push: A is the receiver, so A connects to B ------------
            upload = os.path.join(share_b, "upload.bin")
            upload_payload = os.urandom(120000)
            with open(upload, "wb") as handle:
                handle.write(upload_payload)
            pushed = client.push(upload)
            self.assertEqual(pushed["hash"], ke1024.sha_ke_1024_hex(upload_payload))
            received = os.path.join(cfg_a.receive_dir, "upload.bin")
            self.assertTrue(os.path.isfile(received), "A must receive the push")
            with open(received, "rb") as handle:
                self.assertEqual(handle.read(), upload_payload)

            # ---- missing file / sandbox --------------------------------
            with self.assertRaises(KSClientError) as caught:
                client.get("nope.bin")
            self.assertEqual(caught.exception.err, "notfound")

            with self.assertRaises(KSClientError) as caught:
                client.get("../secret.txt")
            self.assertEqual(caught.exception.err, "denied")
        finally:
            client.close()
            server_a.stop()
            server_b.stop()

    def test_receiver_initiates_data_connection(self):
        """PROTOCOL.md 4.0: get -> the requester connects; put -> the peer connects."""
        folder, share_a, share_b, payload, cfg_a, cfg_b = self._setup()
        log, server_a, server_b, port_a, port_b, client = self._start(
            folder, cfg_a, cfg_b)
        real_open = data.open_data_socket
        try:
            client.connect()
            with mock.patch.object(data, "open_data_socket",
                                   wraps=real_open) as spy:
                client.get("big.bin", os.path.join(share_b, "dir-test.bin"))
                get_target = spy.call_args[0]
                self.assertEqual(get_target[0], "127.0.0.1")
                self.assertEqual(int(get_target[1]), port_a,
                                 "for get the receiver (B) must connect to A")
            upload = os.path.join(share_b, "dir-push.bin")
            with open(upload, "wb") as handle:
                handle.write(os.urandom(20000))
            with mock.patch.object(data, "open_data_socket",
                                   wraps=real_open) as spy:
                client.push(upload)
                push_target = spy.call_args[0]
                self.assertEqual(push_target[0], "127.0.0.1")
                self.assertEqual(int(push_target[1]), port_b,
                                 "for put the receiver (A) must connect to B")
        finally:
            client.close()
            server_a.stop()
            server_b.stop()

    def test_ctl_preamble_full_session(self):
        """``CTL -\\n`` + hello must yield a real session (hello_ack, then list)."""
        folder, share_a, share_b, payload, cfg_a, cfg_b = self._setup()
        log, server_a, server_b, port_a, port_b, client = self._start(
            folder, cfg_a, cfg_b)
        sock = None
        try:
            nonce = "0011223344556677"
            hello = protocol.hello_message(cfg_b.device_id, cfg_b.device_name,
                                           "windows", "test", port_b,
                                           protocol.CAPS_DEFAULT, nonce)
            sock = socket.create_connection(("127.0.0.1", port_a), 10)
            sock.settimeout(10)
            sock.sendall(protocol.ctl_preamble_line() + protocol.encode_ctl(hello))
            ack = self._recv_raw(sock)
            self.assertEqual(ack["t"], "hello_ack")
            self.assertEqual(ack["did"], "AAAAAAAAAAAAAAAA")
            expected = crypto.token_for(cfg_b.device_id, nonce, ack["did"],
                                        ack["nonce"])
            self.assertEqual(ack["token"], expected)
            # same socket must still serve a full session: ask for a listing
            sock.sendall(protocol.encode_ctl(
                {"t": "list", "token": ack["token"], "path": ""}))
            listing = self._recv_raw(sock)
            self.assertEqual(listing["t"], "list_resp")
            self.assertTrue(listing["ok"])
            self.assertIn("big.bin", [e["name"] for e in listing["files"]])
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            client.close(notify=False)
            server_a.stop()
            server_b.stop()

    @staticmethod
    def _recv_raw(sock, timeout=10.0):
        """Read one 4-byte-length prefixed JSON frame from a raw socket."""
        sock.settimeout(timeout)

        def read_exact(count):
            chunks = []
            remaining = count
            while remaining > 0:
                block = sock.recv(remaining)
                if not block:
                    raise AssertionError("socket closed mid-frame")
                chunks.append(block)
                remaining -= len(block)
            return b"".join(chunks)

        (length,) = struct.unpack(">I", read_exact(4))
        return json.loads(read_exact(length).decode("utf-8"))

    def test_list_and_stat(self):
        folder, share_a, share_b, payload, cfg_a, cfg_b = self._setup()
        os.makedirs(os.path.join(share_a, "sub"))
        with open(os.path.join(share_a, "sub", "inner.txt"), "wb") as handle:
            handle.write(b"inner")
        log, server_a, server_b, port_a, port_b, client = self._start(
            folder, cfg_a, cfg_b)
        try:
            client.connect()
            listing = client.list_files("")
            names = sorted(entry["name"] for entry in listing["files"])
            self.assertEqual(names, ["big.bin", "sub"])
            kinds = dict((entry["name"], entry["kind"]) for entry in listing["files"])
            self.assertEqual(kinds["sub"], "dir")
            self.assertEqual(kinds["big.bin"], "file")
            big = [e for e in listing["files"] if e["name"] == "big.bin"][0]
            self.assertEqual(big["size"], len(payload))
            self.assertGreater(big["mtime"], 0)

            inner = client.list_files("sub")
            self.assertEqual([e["name"] for e in inner["files"]], ["inner.txt"])

            info = client.stat("big.bin")
            self.assertEqual(info["path"], "big.bin")
            self.assertEqual(info["size"], len(payload))
            self.assertEqual(info["hash"], ke1024.sha_ke_1024_hex(payload))
            self.assertEqual(len(info["hash"]), 256)

            # sandbox denials must come from the server
            with self.assertRaises(KSClientError) as caught:
                client.list_files("../")
            self.assertEqual(caught.exception.err, "denied")
            with self.assertRaises(KSClientError) as caught:
                client.stat("/etc/passwd")
            self.assertEqual(caught.exception.err, "denied")
            with self.assertRaises(KSClientError) as caught:
                client.stat("does-not-exist.bin")
            self.assertEqual(caught.exception.err, "notfound")
        finally:
            client.close()
            server_a.stop()
            server_b.stop()

    def _lying_send_file(self, real_send):
        """send_file that ships a corrupted byte but reports the good digest."""
        def wrapper(sock, fileobj, size, chunk=protocol.CHUNK_DEFAULT,
                    progress=None, rate=None):
            blob = fileobj.read()
            good = ke1024.sha_ke_1024_hex(blob)
            bad = bytearray(blob)
            if bad:
                bad[0] ^= 0xFF
            result = real_send(sock, io.BytesIO(bytes(bad)), size, chunk=chunk,
                               progress=progress, rate=rate)
            result["digest"] = good
            return result
        return wrapper

    def test_get_hash_mismatch_deletes_file(self):
        folder, share_a, share_b, payload, cfg_a, cfg_b = self._setup()
        log, server_a, server_b, port_a, port_b, client = self._start(
            folder, cfg_a, cfg_b)
        real_send = data.send_file
        try:
            client.connect()
            with mock.patch.object(data, "send_file",
                                   self._lying_send_file(real_send)):
                with self.assertRaises(KSClientError) as caught:
                    client.get("big.bin", os.path.join(share_b, "corrupt.bin"))
            self.assertEqual(caught.exception.err, "checksum")
            self.assertFalse(os.path.exists(os.path.join(share_b, "corrupt.bin")))
            self.assertFalse(os.path.exists(os.path.join(share_b, "corrupt.bin.part")))
        finally:
            client.close()
            server_a.stop()
            server_b.stop()

    def test_push_hash_mismatch_deletes_file(self):
        folder, share_a, share_b, payload, cfg_a, cfg_b = self._setup()
        log, server_a, server_b, port_a, port_b, client = self._start(
            folder, cfg_a, cfg_b)
        real_send = data.send_file
        try:
            client.connect()
            upload = os.path.join(share_b, "corrupt-src.bin")
            with open(upload, "wb") as handle:
                handle.write(os.urandom(70000))
            with mock.patch.object(data, "send_file",
                                   self._lying_send_file(real_send)):
                with self.assertRaises(KSClientError) as caught:
                    client.push(upload)
            self.assertEqual(caught.exception.err, "checksum")
            target = os.path.join(cfg_a.receive_dir, "corrupt-src.bin")
            self.assertFalse(os.path.exists(target),
                             "corrupt file must be deleted by the receiver")
            self.assertFalse(os.path.exists(target + ".part"))
        finally:
            client.close()
            server_a.stop()
            server_b.stop()

    def test_upload_denied_when_disabled(self):
        folder, share_a, share_b, payload, cfg_a, cfg_b = self._setup()
        cfg_a.data["share"]["allow_upload"] = False      # A refuses pushes
        cfg_a.normalise()
        log, server_a, server_b, port_a, port_b, client = self._start(
            folder, cfg_a, cfg_b)
        try:
            client.connect()
            upload = os.path.join(share_b, "blocked.bin")
            with open(upload, "wb") as handle:
                handle.write(b"x" * 1024)
            with self.assertRaises(KSClientError) as caught:
                client.push(upload)
            self.assertEqual(caught.exception.err, "denied")
        finally:
            client.close()
            server_a.stop()
            server_b.stop()

