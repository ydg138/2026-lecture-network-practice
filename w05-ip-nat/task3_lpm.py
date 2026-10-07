#!/usr/bin/env python3
"""Week 5 · Task 3 — Make longest-prefix match fast.

Textbook §4.3.3.

`LinearTable` is correct and it is what you probably wrote in Task 1: keep the
prefixes in a list, check every one, remember the longest that matched. On six
entries that is fine. A real router holds close to a million, and it has to
answer while the packet is still in the buffer.

Beat it:

    python3 bench.py
    python3 bench.py --yours

Correctness first: `bench.py` checks every one of your answers against the
linear table. A fast router that forwards to the wrong next hop is not a
router, it is an outage.
"""


class LinearTable:
    """Correct, and slow in the obvious way."""

    def __init__(self):
        self.entries = []                     # (prefix_len, network, next_hop)

    def add(self, network, prefix_len, next_hop):
        self.entries.append((prefix_len, network, next_hop))

    def lookup(self, address):
        best = None
        for plen, net, hop in self.entries:
            mask = (0xFFFFFFFF << (32 - plen)) & 0xFFFFFFFF
            if address & mask == net and (best is None or plen > best[0]):
                best = (plen, hop)
        return best[1] if best else None


class YourTable:
    """Group by prefix length: one hash table per length that is in use.

    add():    tables[plen][network] = next_hop, and keep the list of lengths
              sorted longest-first.
    lookup(): for each length in use, longest first, mask the address and do
              one dict lookup. The first hit is the longest match, so stop.

    Work per lookup = number of *distinct prefix lengths* in the table
    (at most 33, here 7), not the number of routes (5,000).
    Memory: one dict entry per route, plus at most 33 small dicts.
    """

    def __init__(self):
        self.tables = {}          # plen -> {network: next_hop}
        self.order = []           # [(mask, table)] longest prefix first

    def add(self, network, prefix_len, next_hop):
        t = self.tables.get(prefix_len)
        if t is None:
            t = self.tables[prefix_len] = {}
            self.order = [((0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF, self.tables[p])
                          for p in sorted(self.tables, reverse=True)]
        t[network] = next_hop

    def lookup(self, address):
        for mask, table in self.order:
            hop = table.get(address & mask)
            if hop is not None:
                return hop
        return None


class TrieTable:
    """For comparison: a binary trie, one bit per level (what hardware builds).

    Work per lookup = at most 32 steps (the address width), whatever the
    table size. Not used by bench.py - try it with  YourTable = TrieTable.
    """

    def __init__(self):
        self.root = [None, None, None]        # [child0, child1, next_hop]

    def add(self, network, prefix_len, next_hop):
        node = self.root
        for i in range(prefix_len):
            bit = (network >> (31 - i)) & 1
            if node[bit] is None:
                node[bit] = [None, None, None]
            node = node[bit]
        node[2] = next_hop

    def lookup(self, address):
        node, best, i = self.root, None, 31
        while node is not None:
            if node[2] is not None:
                best = node[2]
            if i < 0:
                break
            node = node[(address >> i) & 1]
            i -= 1
        return best