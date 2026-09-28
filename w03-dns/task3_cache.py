#!/usr/bin/env python3
"""Week 3 · Task 3 — Beat the baseline cache.

Textbook §2.4.2 (caching) and §2.4.3 (TTL).

`BaselineCache` below works. It is also bad, in more than one way, and one of
its problems is worse than being slow. Find them, write `YourCache`, and prove
the improvement with the harness:

    python3 bench.py                 # baseline only
    python3 bench.py --yours         # baseline vs. yours, side by side

Rules
-----
* Do not change `bench.py`. If you need to change it to win, you are not
  winning. Say so in observation.md instead.
* `YourCache` must expose the same two methods as `BaselineCache`.
* Speed is not the only score. The harness also counts **stale answers** -
  times you served a record whose TTL had already run out. A cache that keeps
  everything forever is very fast and completely wrong.

Targets
-------
The baseline scores **325 upstream queries, 67.5% hit rate, 266 stale answers**.

  pass  : zero stale answers
  good  : zero stale, and no more upstream queries than the baseline
  strong: the above, plus you can say in observation.md **how few upstream
          queries a correct cache could possibly make on this workload, and
          why you cannot go below that number**

That last one is the real question. Read it before you start optimising -
it will tell you where to stop.
"""
import time


class BaselineCache:
    """A DNS cache that somebody wrote in a hurry.

    It caches. It is not correct, and it is not fast. Both are your problem.
    """

    FIXED_LIFETIME = 60          # seconds we keep anything, regardless of TTL

    def __init__(self, upstream):
        self.upstream = upstream  # upstream(name) -> (address, ttl)
        self.entries = []         # list of [name, address, stored_at]

    def lookup(self, name, now):
        """Return an address for `name`, asking upstream only if we have to."""
        for entry in self.entries:                      # linear scan
            if entry[0] == name:
                if now - entry[2] < self.FIXED_LIFETIME:
                    return entry[1]
                self.entries.remove(entry)
                break
        address, ttl = self.upstream(name)
        self.entries.append([name, address, now])
        return address

    def stats(self):
        return {"entries": len(self.entries)}


class YourCache:
    """A TTL-respecting DNS cache.

    Fixes to the baseline, both with the same root cause (it ignores the TTL
    that upstream hands back and uses one fixed 60 s lifetime instead):

    * correctness: a record with TTL < 60 s (www.microsoft.com 20 s,
      www.cnn.com 30 s) was served after it had expired -> stale answers.
    * performance: a record with TTL > 60 s (up to 86400 s) was thrown away
      after 60 s and fetched again although it was still valid.

    Here every entry lives exactly as long as its own TTL says. Lookup is a
    dict (O(1)) instead of a linear scan over a list.
    """

    def __init__(self, upstream):
        self.upstream = upstream  # upstream(name) -> (address, ttl)
        self.entries = {}         # name -> (address, expires_at)
        self.hits = 0
        self.misses = 0

    def lookup(self, name, now):
        entry = self.entries.get(name)
        if entry is not None:
            address, expires_at = entry
            if now <= expires_at:         # still inside its TTL -> serve it
                self.hits += 1
                return address
            del self.entries[name]        # expired: never serve it
        address, ttl = self.upstream(name)
        self.misses += 1
        self.entries[name] = (address, now + ttl)
        return address

    def stats(self):
        return {"entries": len(self.entries), "hits": self.hits,
                "misses": self.misses}