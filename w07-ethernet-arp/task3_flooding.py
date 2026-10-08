#!/usr/bin/env python3
"""Week 7 · Task 3 — A switch that runs out of room.

Textbook §6.4.3.

A switch does not have room for every station on the network. Its forwarding
table sits in a fixed amount of fast memory, and when that fills, something has
to go. Whatever goes gets **flooded** the next time anyone sends to it - out of
every port, to every station that did not ask for it.

`NaiveSwitch` handles a full table the simplest way: throw out whichever entry
has been there longest. That is a perfectly reasonable first idea and it is
close to the worst thing you can do with this traffic.

    python3 bench.py
    python3 bench.py --yours

Correctness first: every frame whose destination is reachable must still reach
it. A switch that floods less because it delivers less is not a switch.
"""
import hashlib
from collections import OrderedDict


class NaiveSwitch:
    """Correct. Evicts the oldest entry, which is not the least useful one."""

    BROADCAST = "ff:ff:ff:ff:ff:ff"

    def __init__(self, ports, capacity):
        self.ports = ports
        self.capacity = capacity
        self.table = OrderedDict()                 # mac -> port, insertion order

    def handle(self, frame, in_port):
        src, dst, now = frame
        if src not in self.table and len(self.table) >= self.capacity:
            self.table.popitem(last=False)         # throw out the oldest arrival
        self.table[src] = in_port

        if dst == self.BROADCAST or dst not in self.table:
            return [p for p in range(self.ports) if p != in_port]
        out = self.table[dst]
        return [] if out == in_port else [out]


class YourSwitch:
    """Your switch. Same interface, same deliveries, far less flooding.

        __init__(ports, capacity)
        handle((src, dst, time), in_port) -> ports to forward out of

    `self.table` must exist and must never hold more than `capacity` entries.
    The harness checks this after every frame.

    What the baseline gets wrong: it decides what to keep by **when the entry
    arrived**, and that has nothing to do with whether it is about to be needed.
    On this trace a handful of stations carry most of the traffic, and the
    baseline keeps throwing them out to make room for stations that spoke once.

    Two questions worth separating:

      * which entry should leave when the table is full?
      * should learning a new source ever be *declined*?

    The second one is less obvious and is worth thinking about before you
    dismiss it.

    Note what you are NOT allowed to do: you cannot look at `where`, you cannot
    see the future, and you cannot hold more than `capacity` entries. Everything
    you know comes from the frames you have already handled.
    """

    BROADCAST = "ff:ff:ff:ff:ff:ff"

    def __init__(self, ports, capacity):
        self.ports = ports
        self.capacity = capacity
        self.table = OrderedDict()      # mac -> port, least recently used first
        self.sketch = FrequencySketch()
        self.port_quota = max(1, capacity // 8)   # no port may hold more than 8 of 64
        self.admitted = self.declined = 0

    def handle(self, frame, in_port):
        src, dst, now = frame

        # Evidence: how often each MAC shows up, as source or as destination.
        # A destination we had to flood is exactly the demand a slot would save.
        self.sketch.add(src)
        if dst != self.BROADCAST:
            self.sketch.add(dst)
            if dst in self.table:
                self.table.move_to_end(dst)          # just wanted: most recent

        if src in self.table:
            self.table[src] = in_port                # refresh, and relearn a move
            self.table.move_to_end(src)
        else:
            same_port = [mac for mac, port in self.table.items() if port == in_port]
            if len(same_port) >= self.port_quota:
                # This port already has its share: a newcomer from here can
                # only replace this port's own least recently used entry.
                victim = same_port[0]
            elif len(self.table) < self.capacity:
                victim = None
            else:
                # Eviction: the least recently used entry, not the oldest arrival.
                victim = next(iter(self.table))

            # Admission: the newcomer only takes a slot from someone if it has
            # been seen more often than the entry it would push out. A station
            # heard once does not get to evict one that is wanted all the time.
            if victim is None:
                self.table[src] = in_port
                self.admitted += 1
            elif self.sketch.estimate(src) > self.sketch.estimate(victim):
                del self.table[victim]
                self.table[src] = in_port
                self.admitted += 1
            else:
                self.declined += 1

        if dst == self.BROADCAST or dst not in self.table:
            return [p for p in range(self.ports) if p != in_port]
        out = self.table[dst]
        return [] if out == in_port else [out]


class FrequencySketch:
    """Approximate per-MAC counts in a fixed amount of memory.

    A count-min sketch: `depth` rows of `width` counters, each MAC hashed to one
    counter per row, estimate = the smallest of its counters (collisions can
    only push counts up). Conservative update raises only the counters that are
    at that minimum, which keeps rare MACs from looking popular.

    Memory never grows, however many MACs appear - 4 x 256 counters here - and
    every `halve_every` updates all counters are halved, so old popularity
    fades instead of holding a slot forever.
    """

    def __init__(self, width=256, depth=4, halve_every=3_200):
        self.width, self.depth = width, depth
        self.rows = [[0] * width for _ in range(depth)]
        self.halve_every = halve_every
        self.updates = 0

    def _slots(self, mac):
        digest = hashlib.blake2b(mac.encode(), digest_size=2 * self.depth).digest()
        return [int.from_bytes(digest[2 * i:2 * i + 2], "little") % self.width
                for i in range(self.depth)]

    def add(self, mac):
        slots = self._slots(mac)
        low = min(row[i] for row, i in zip(self.rows, slots))
        for row, i in zip(self.rows, slots):
            if row[i] == low:
                row[i] += 1
        self.updates += 1
        if self.updates >= self.halve_every:
            self.updates = 0
            for row in self.rows:
                for i in range(self.width):
                    row[i] >>= 1

    def estimate(self, mac):
        return min(row[i] for row, i in zip(self.rows, self._slots(mac)))