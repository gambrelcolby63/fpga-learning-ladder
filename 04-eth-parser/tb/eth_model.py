"""Frame builder and reference parser for the eth_udp_parser testbench.

build_*() create Ethernet II frames as bytes (FCS already stripped, like a MAC hands them over).
parse() is the reference model: it returns (header_tuple, payload_bytes) for frames the DUT must
forward, or None for frames it must drop. It works on raw bytes only (independent of the builder).
"""
from __future__ import annotations

import random
import struct
from typing import NamedTuple

ETH_IPV4, ETH_ARP, ETH_VLAN, ETH_IPV6 = 0x0800, 0x0806, 0x8100, 0x86DD
PROTO_ICMP, PROTO_TCP, PROTO_UDP = 1, 6, 17


class Hdr(NamedTuple):
    dst_mac: int
    src_mac: int
    ethertype: int
    ip_src: int
    ip_dst: int
    udp_src_port: int
    udp_dst_port: int
    udp_len: int

    def __str__(self):
        return (f"dst={self.dst_mac:012x} src={self.src_mac:012x} type={self.ethertype:04x} "
                f"ip {self.ip_src:08x}->{self.ip_dst:08x} udp {self.udp_src_port}->{self.udp_dst_port} "
                f"len={self.udp_len}")


def rand_mac() -> bytes:
    return bytes(random.getrandbits(8) for _ in range(6))


def ip_checksum(hdr: bytes) -> int:
    s = sum(struct.unpack(f"!{len(hdr) // 2}H", hdr))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def build_udp(payload: bytes, ihl: int = 5, proto: int = PROTO_UDP, version: int = 4,
              ethertype: int = ETH_IPV4, udp_len: int | None = None, pad_to: int = 60,
              trailer: bytes = b"", sport: int | None = None, dport: int | None = None) -> bytes:
    """Ethernet II + IPv4 (ihl*4 bytes incl. options) + UDP + payload, padded to pad_to bytes."""
    sport = random.getrandbits(16) if sport is None else sport
    dport = random.getrandbits(16) if dport is None else dport
    ulen = 8 + len(payload) if udp_len is None else udp_len
    udp = struct.pack("!HHHH", sport, dport, ulen & 0xFFFF, 0) + payload
    options = bytes(random.getrandbits(8) for _ in range(max(0, ihl - 5) * 4))
    total_len = (max(ihl, 5) * 4 + len(udp)) & 0xFFFF
    ip = struct.pack("!BBHHHBBH4s4s", (version << 4) | (ihl & 0xF), 0, total_len,
                     random.getrandbits(16), 0x4000, 64, proto, 0,
                     random.getrandbits(32).to_bytes(4, "big"), random.getrandbits(32).to_bytes(4, "big"))
    ip = ip + options
    ip = ip[:10] + struct.pack("!H", ip_checksum(ip)) + ip[12:]
    frame = rand_mac() + rand_mac() + struct.pack("!H", ethertype) + ip + udp
    if len(frame) < pad_to:
        frame += bytes(pad_to - len(frame))
    return frame + trailer


def build_other(kind: str) -> bytes:
    """Frames that must be dropped."""
    if kind == "arp":
        body = bytes(random.getrandbits(8) for _ in range(28))
        return (rand_mac() + rand_mac() + struct.pack("!H", ETH_ARP) + body).ljust(60, b"\0")
    if kind == "ipv6":
        body = bytes(random.getrandbits(8) for _ in range(random.randint(40, 200)))
        return rand_mac() + rand_mac() + struct.pack("!H", ETH_IPV6) + body
    if kind == "vlan_udp":  # 802.1Q-tagged UDP: dropped in the base spec (stretch goal)
        f = build_udp(bytes(random.getrandbits(8) for _ in range(random.randint(0, 80))))
        return f[:12] + struct.pack("!HH", ETH_VLAN, random.getrandbits(12)) + f[12:]
    if kind == "tcp":
        return build_udp(bytes(random.getrandbits(8) for _ in range(random.randint(20, 120))), proto=PROTO_TCP)
    if kind == "icmp":
        return build_udp(bytes(random.getrandbits(8) for _ in range(random.randint(0, 64))), proto=PROTO_ICMP)
    if kind == "ip_version6_in_v4_type":
        return build_udp(bytes(random.getrandbits(8) for _ in range(20)), version=6)
    if kind == "ihl_too_small":
        return build_udp(bytes(random.getrandbits(8) for _ in range(20)), ihl=random.randint(0, 4))
    if kind == "udp_len_too_small":
        return build_udp(bytes(random.getrandbits(8) for _ in range(20)), udp_len=random.randint(0, 7))
    if kind == "udp_wrong_ethertype":  # perfectly good IPv4/UDP bytes behind a non-IPv4 ethertype
        et = random.choice([ETH_ARP, ETH_IPV6, 0x88B5, 0x0801, 0x0900 | random.getrandbits(8)])
        return build_udp(bytes(random.getrandbits(8) for _ in range(random.randint(0, 64))), ethertype=et)
    if kind == "runt":  # ends before the UDP header is complete
        f = build_udp(bytes(20), pad_to=0)
        return f[:random.randint(1, 41)]
    raise ValueError(kind)


OTHER_KINDS = ["arp", "ipv6", "vlan_udp", "tcp", "icmp", "ip_version6_in_v4_type", "ihl_too_small",
               "udp_len_too_small", "udp_wrong_ethertype", "runt"]


def parse(frame: bytes):
    """Reference model. Returns (Hdr, payload) or None if the frame must be dropped."""
    if len(frame) < 14:
        return None
    ethertype = int.from_bytes(frame[12:14], "big")
    if ethertype != ETH_IPV4 or len(frame) < 15:
        return None
    ver, ihl = frame[14] >> 4, frame[14] & 0xF
    if ver != 4 or ihl < 5:
        return None
    udp_off = 14 + 4 * ihl
    if len(frame) < udp_off + 8 or frame[23] != PROTO_UDP:
        return None
    sport, dport, ulen = struct.unpack("!HHH", frame[udp_off:udp_off + 6])
    if ulen < 8:
        return None
    hdr = Hdr(int.from_bytes(frame[0:6], "big"), int.from_bytes(frame[6:12], "big"), ethertype,
              int.from_bytes(frame[26:30], "big"), int.from_bytes(frame[30:34], "big"), sport, dport, ulen)
    payload = frame[udp_off + 8:udp_off + ulen]  # truncates Ethernet padding; short if frame truncated
    return hdr, payload
