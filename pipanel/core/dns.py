"""DNS resolution checks: directly against dnsmasq and through the system resolver."""
from __future__ import annotations

import random
import socket
import struct


def query_a(host: str, *, server: str = "127.0.0.1", port: int = 5354, timeout: float = 1.5) -> list[str]:
    """Send a raw A query over UDP; return the IPv4 list (empty if there is no answer)."""
    tid = random.randrange(0x10000)
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for label in host.rstrip(".").split("."):
        q += bytes([len(label)]) + label.encode("ascii")
    q += b"\x00" + struct.pack(">HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(q, (server, port))
            data, _ = s.recvfrom(2048)
        except OSError:
            return []
    if len(data) < 12 or struct.unpack(">H", data[:2])[0] != tid:
        return []
    an = struct.unpack(">H", data[6:8])[0]
    i = 12
    while data[i] != 0:          # skip QNAME
        i += data[i] + 1
    i += 1 + 4
    ips = []
    for _ in range(an):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i] != 0:
                i += data[i] + 1
            i += 1
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            ips.append(".".join(map(str, data[i:i + 4])))
        i += rdlen
    return ips


def system_resolves(host: str) -> bool:
    try:
        return "127.0.0.1" in {a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)}
    except OSError:
        return False
