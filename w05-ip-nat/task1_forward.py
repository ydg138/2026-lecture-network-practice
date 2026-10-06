#!/usr/bin/env python3
"""Week 5 · Task 1 — Subnets and longest-prefix match.

Textbook §4.3.2 (IPv4 addressing, CIDR) and §4.3.3 (forwarding).

Two things a router does with every packet: work out which prefixes the
destination falls inside, and pick the longest one. The second is the whole
of "longest prefix match", and it is the reason the internet's routing table
can hold a million entries and still be answerable.

You build both, from integers up. No `ipaddress` module - that library is
exactly the thing you are supposed to understand this week.

    python3 task1_forward.py --verify
"""
import argparse


MASK32 = 0xFFFFFFFF


def ip_to_int(s):
    """'10.20.30.70' -> 32-bit int. Shifts only, no ipaddress / inet_aton."""
    parts = s.strip().split(".")
    if len(parts) != 4:
        raise ValueError(f"not a dotted quad: {s!r}")
    n = 0
    for p in parts:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            raise ValueError(f"bad octet {p!r} in {s!r}")
        n = (n << 8) | int(p)
    return n


def int_to_ip(n):
    """32-bit int -> 'a.b.c.d'."""
    return ".".join(str((n >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def prefix_mask(plen):
    """/24 -> 0xFFFFFF00. /0 -> 0 (shift by 32 would leave junk, so mask it)."""
    return (MASK32 << (32 - plen)) & MASK32


def parse_cidr(cidr):
    """'163.152.6.0/24' -> (network as int, prefix length).

    Requirements: reject a prefix length outside 0-32, and reject an address
    whose host bits are set when they should not be (163.152.6.5/24 is a
    common way to write a host, but it is not a network).
    """
    if "/" not in cidr:
        raise ValueError(f"no prefix length in {cidr!r}")
    addr_s, plen_s = cidr.split("/", 1)
    if not plen_s.isdigit():
        raise ValueError(f"bad prefix length in {cidr!r}")
    plen = int(plen_s)
    if not 0 <= plen <= 32:                                   # R1
        raise ValueError(f"prefix length {plen} outside 0-32")
    addr = ip_to_int(addr_s)
    if addr & ~prefix_mask(plen) & MASK32:                    # R2
        raise ValueError(f"{cidr} has host bits set - it is a host, not a network")
    return addr, plen


def network_range(cidr):
    """'163.152.6.0/24' -> (first usable, last usable, broadcast) as strings.

    /31 (RFC 3021, point-to-point): both addresses are usable, no broadcast
        -> (network, network+1, None)
    /32 (a single host route): the one address is the only host, no broadcast
        -> (addr, addr, None)
    """
    net, plen = parse_cidr(cidr)
    last = net | (~prefix_mask(plen) & MASK32)                # all host bits = 1
    if plen == 32:
        return (int_to_ip(net), int_to_ip(net), None)
    if plen == 31:
        return (int_to_ip(net), int_to_ip(last), None)
    return (int_to_ip(net + 1), int_to_ip(last - 1), int_to_ip(last))


class ForwardingTable:
    """Longest-prefix-match forwarding.

    add(cidr, next_hop)  ·  lookup(address) -> next_hop or None

    The default route 0.0.0.0/0 matches everything and is the shortest prefix,
    so it must lose to any other match. If two entries have the same prefix
    length, the table is malformed - say what you do.

    Same (network, prefix length) added twice: the later add replaces the
    earlier one, the way a route update overwrites the old route.
    """

    def __init__(self):
        self.routes = {}                      # (network, plen) -> next_hop

    def add(self, cidr, next_hop):
        net, plen = parse_cidr(cidr)
        self.routes[(net, plen)] = next_hop

    def matches(self, address):
        """Every entry the address falls inside, longest first."""
        a = ip_to_int(address) if isinstance(address, str) else address
        hits = [(plen, net, hop) for (net, plen), hop in self.routes.items()
                if a & prefix_mask(plen) == net]
        return sorted(hits, reverse=True)

    def lookup(self, address):
        hits = self.matches(address)
        return hits[0][2] if hits else None   # longest prefix wins (R4, R5)


# ------------------------------------------------------------------- harness
RANGE_CASES = [
    ("192.168.0.0/24",  "192.168.0.1",   "192.168.0.254",  "192.168.0.255"),
    ("10.0.0.0/8",      "10.0.0.1",      "10.255.255.254", "10.255.255.255"),
    ("172.16.32.0/20",  "172.16.32.1",   "172.16.47.254",  "172.16.47.255"),
    ("203.0.113.64/26", "203.0.113.65",  "203.0.113.126",  "203.0.113.127"),
]

TABLE = [
    ("0.0.0.0/0",       "default-gw"),
    ("10.0.0.0/8",      "campus"),
    ("10.20.0.0/16",    "eng-building"),
    ("10.20.30.0/24",   "lab-floor"),
    ("10.20.30.64/26",  "lab-rack-2"),
    ("192.168.1.0/24",  "home"),
]

LOOKUP_CASES = [
    ("10.20.30.70",   "lab-rack-2"),     # inside all four 10.x entries
    ("10.20.30.10",   "lab-floor"),
    ("10.20.99.1",    "eng-building"),
    ("10.99.0.1",     "campus"),
    ("8.8.8.8",       "default-gw"),
    ("192.168.1.77",  "home"),
]


def verify():
    fails = 0
    for cidr, first, last, bcast in RANGE_CASES:
        try:
            got = network_range(cidr)
        except NotImplementedError:
            print("  network_range is still a stub"); return 1
        except Exception as e:
            print(f"  FAIL  {cidr:<18} raised {e!r}"); fails += 1; continue
        ok = tuple(got) == (first, last, bcast)
        print(f"  {'ok  ' if ok else 'FAIL'}  {cidr:<18} {got}")
        fails += not ok

    t = ForwardingTable()
    try:
        for cidr, hop in TABLE:
            t.add(cidr, hop)
    except NotImplementedError:
        print("  ForwardingTable is still a stub"); return 1

    for addr, expect in LOOKUP_CASES:
        got = t.lookup(addr)
        ok = got == expect
        print(f"  {'ok  ' if ok else 'FAIL'}  {addr:<16} -> {got}  (want {expect})")
        fails += not ok

    print(f"\n  {len(RANGE_CASES) + len(LOOKUP_CASES) - fails}"
          f"/{len(RANGE_CASES) + len(LOOKUP_CASES)} ok")
    return 1 if fails else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true")
    a = p.parse_args()
    raise SystemExit(verify() if a.verify else p.print_help())