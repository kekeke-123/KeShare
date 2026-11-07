# -*- coding: utf-8 -*-
"""Minimal mDNS (DNS-SD) codec for ``_keshare._tcp.local``.

Only the three record types KeShare needs are implemented: PTR (service type ->
instance), SRV (instance -> port/host) and TXT (key=value capability data).
Name compression pointers are understood when parsing (receivers must support
them); emitted packets do not use compression so they stay trivially readable.

This module is pure encoding/decoding -- socket handling lives in
:mod:`keshare.discovery`.
"""

import struct

SERVICE_TYPE = "_keshare._tcp.local"
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

DNS_A = 1
DNS_PTR = 12
DNS_TXT = 16
DNS_SRV = 33


def encode_name(name):
    """Encode a dotted DNS name (no compression)."""
    out = bytearray()
    for label in str(name).split("."):
        if not label:
            continue
        raw = label.encode("utf-8")[:63]
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def decode_name(data, offset, depth=0):
    """Decode a (possibly compressed) DNS name; returns ``(name, next_offset)``."""
    if depth > 8:
        raise ValueError("dns name pointer loop")
    labels = []
    jumped = False
    next_offset = offset
    while True:
        if offset >= len(data):
            raise ValueError("dns name out of range")
        length = data[offset]
        if length == 0:
            offset += 1
            if not jumped:
                next_offset = offset
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated dns pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                next_offset = offset + 2
            jumped = True
            offset = pointer
            continue
        offset += 1
        label = data[offset:offset + length]
        if len(label) != length:
            raise ValueError("truncated dns label")
        labels.append(label.decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), next_offset


def txt_strings(raw):
    """Split a TXT rdata blob into its length-prefixed strings."""
    out = []
    index = 0
    while index < len(raw):
        size = raw[index]
        index += 1
        if size == 0:
            continue
        out.append(raw[index:index + size].decode("utf-8", "replace"))
        index += size
    return out


def build_response(instance, host, port, txt):
    """Encode a PTR+SRV+TXT response for :data:`SERVICE_TYPE`."""
    instance_name = "%s.%s" % (instance, SERVICE_TYPE)
    records = []

    rdata = encode_name(instance_name)
    records.append(encode_name(SERVICE_TYPE)
                   + struct.pack(">HHIH", DNS_PTR, 0x0001, 120, len(rdata)) + rdata)

    srv = struct.pack(">HHH", 0, 0, int(port)) + encode_name(host)
    records.append(encode_name(instance_name)
                   + struct.pack(">HHIH", DNS_SRV, 0x8001, 120, len(srv)) + srv)

    txt_raw = bytearray()
    for key, value in txt:
        item = ("%s=%s" % (key, value)).encode("utf-8")
        txt_raw.append(len(item) & 0xFF)
        txt_raw.extend(item)
    records.append(encode_name(instance_name)
                   + struct.pack(">HHIH", DNS_TXT, 0x8001, 120, len(txt_raw))
                   + bytes(txt_raw))

    header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0)
    return header + b"".join(records)


def parse_packet(data):
    """Return ``(questions, records)`` from an mDNS packet."""
    if len(data) < 12:
        return [], []
    _ident, _flags, qdcount, ancount, nscount, arcount = struct.unpack(
        ">HHHHHH", data[:12])
    offset = 12
    questions = []
    for _ in range(qdcount):
        name, offset = decode_name(data, offset)
        if offset + 4 > len(data):
            break
        qtype, _qclass = struct.unpack(">HH", data[offset:offset + 4])
        offset += 4
        questions.append({"name": name, "type": qtype})
    records = []
    for _ in range(ancount + nscount + arcount):
        name, offset = decode_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlength = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlength]
        offset += rdlength
        record = {"name": name, "type": rtype, "data": rdata}
        if rtype == DNS_PTR:
            try:
                record["target"], _ = decode_name(data, offset - rdlength)
            except ValueError:
                record["target"] = ""
        elif rtype == DNS_SRV and len(rdata) >= 6:
            try:
                record["port"] = struct.unpack(">H", rdata[4:6])[0]
                record["target"], _ = decode_name(data, offset - rdlength + 6)
            except ValueError:
                record["port"] = 0
        elif rtype == DNS_TXT:
            record["txt"] = txt_strings(rdata)
        records.append(record)
    return questions, records
