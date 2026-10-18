"""A tiny DNS server for *.test on Windows and macOS (Linux uses dnsmasq).

It answers A queries for <anything>.<tld> with 127.0.0.1, AAAA queries with no address (so browsers fall back to
IPv4 at once), and refuses everything else. The system sends it only the .test names: on Windows through a Name
Resolution Policy Table rule, on macOS through /etc/resolver/test.
"""
from __future__ import annotations

import socket
import struct

A, AAAA = 1, 28


def parse_question(data: bytes) -> tuple[int, str, int, int] | None:
    """(transaction id, name, qtype, end of the question section) of a query, or None when it is not one."""
    if len(data) < 17:
        return None
    tid, flags, qd = struct.unpack(">HHH", data[:6])
    if flags & 0x8000 or qd != 1:   # a response, or not exactly one question
        return None
    labels, i = [], 12
    while i < len(data) and data[i]:
        n = data[i]
        if n & 0xC0 or i + 1 + n > len(data):
            return None
        labels.append(data[i + 1:i + 1 + n].decode("ascii", "replace"))
        i += 1 + n
    if i + 5 > len(data):
        return None
    qtype = struct.unpack(">H", data[i + 1:i + 3])[0]
    return tid, ".".join(labels).lower(), qtype, i + 5


def answer(data: bytes, tld: str = "test", address: str = "127.0.0.1") -> bytes | None:
    q = parse_question(data)
    if q is None:
        return None
    tid, name, qtype, end = q
    question = data[12:end]
    ours = name == tld or name.endswith("." + tld)
    if not ours:   # REFUSED: not ours to answer
        return struct.pack(">HHHHHH", tid, 0x8185, 1, 0, 0, 0) + question
    if qtype != A:   # the name exists, but has no record of this type (NOERROR, no answer)
        return struct.pack(">HHHHHH", tid, 0x8580, 1, 0, 0, 0) + question
    record = struct.pack(">HHHIH", 0xC00C, A, 1, 60, 4) + socket.inet_aton(address)
    return struct.pack(">HHHHHH", tid, 0x8580, 1, 1, 0, 0) + question + record


def serve(port: int = 53, tld: str = "test", host: str = "127.0.0.1", ready=None) -> None:
    """Answer forever (the agent stops it)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((host, port))
    if ready:
        ready()
    while True:
        try:
            data, addr = s.recvfrom(512)
        except OSError:   # Windows reports an ICMP "port unreachable" from an earlier reply this way
            continue
        reply = answer(data, tld)
        if reply:
            try:
                s.sendto(reply, addr)
            except OSError:
                pass


def main(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="pipanel _dns")
    p.add_argument("--port", type=int, default=53)
    p.add_argument("--tld", default="test")
    a = p.parse_args(argv)
    serve(a.port, a.tld)
    return 0
