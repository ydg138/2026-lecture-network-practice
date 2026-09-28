#!/usr/bin/env python3
"""Week 3 · Task 1 — Build your own iterative resolver.

Textbook §2.4.2 - §2.4.3.

This version performs the DNS hierarchy walk itself:
root -> TLD -> authoritative server.
It never asks a public/local recursive resolver to do the lookup for it.
"""
import argparse
import subprocess
import sys

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rdatatype


ROOT_SERVERS = [
    "198.41.0.4",       # a.root-servers.net
    "199.9.14.201",     # b.root-servers.net
    "192.33.4.12",      # c.root-servers.net
]

VERIFY_NAMES = [
    ("www.korea.ac.kr", "stable"),
    ("dns.google", "stable"),
    ("en.wikipedia.org", "stable"),
    ("www.stanford.edu", "stable"),
    ("www.microsoft.com", "cdn"),
]


class Resolver:
    """A small iterative DNS resolver using non-recursive DNS queries."""

    def __init__(self, timeout=2.0, max_depth=20):
        self.timeout = timeout
        self.max_depth = max_depth

    @staticmethod
    def _normalise_name(name):
        """Return a fully-qualified DNS name with a trailing dot."""
        if not name.endswith("."):
            return name + "."
        return name

    def _query(self, server, name, path):
        """Ask one server for an A record without recursion."""
        # R4: record every server we actually try, including one that times out.
        path.append(server)

        qname = dns.name.from_text(self._normalise_name(name))
        query = dns.message.make_query(qname, dns.rdatatype.A)
        # The important part of this lab: do NOT ask the server to recurse.
        query.flags &= ~dns.flags.RD

        try:
            response = dns.query.udp(query, server, timeout=self.timeout)
        except (dns.exception.Timeout, OSError, EOFError) as exc:
            raise TimeoutError(f"DNS server {server} did not answer: {exc}") from exc

        # Some responses may be truncated over UDP. Retry over TCP.
        if response.flags & dns.flags.TC:
            response = dns.query.tcp(query, server, timeout=self.timeout)

        return response

    @staticmethod
    def _answer_a(response, qname):
        """Return an A record in the answer that corresponds to qname."""
        for rrset in response.answer:
            if rrset.rdtype == dns.rdatatype.A and rrset.name == qname:
                for rr in rrset:
                    return rr.address
        return None

    @staticmethod
    def _answer_cname(response, qname):
        """Return a CNAME target if qname is answered by a CNAME."""
        for rrset in response.answer:
            if rrset.rdtype == dns.rdatatype.CNAME and rrset.name == qname:
                for rr in rrset:
                    return rr.target.to_text()
        return None

    @staticmethod
    def _referral(response):
        """Return NS names from the authority section, if this is a referral."""
        ns_names = []
        for rrset in response.authority:
            if rrset.rdtype != dns.rdatatype.NS:
                continue
            for rr in rrset:
                ns_names.append(rr.target.to_text())
        return ns_names

    @staticmethod
    def _glue_addresses(response, ns_names):
        """Return glue A records for the referred nameservers."""
        wanted = {dns.name.from_text(n) for n in ns_names}
        addresses = {}
        for rrset in response.additional:
            if rrset.rdtype != dns.rdatatype.A or rrset.name not in wanted:
                continue
            addresses.setdefault(rrset.name.to_text(), [])
            addresses[rrset.name.to_text()].extend(rr.address for rr in rrset)
        return addresses

    def _resolve_ns_name(self, ns_name, path, depth):
        """Resolve a nameserver hostname when a delegation has no glue."""
        if depth >= self.max_depth:
            raise RuntimeError("maximum resolution depth reached while resolving nameserver")
        # R3: no glue means we perform another complete iterative walk for the
        # nameserver's own hostname, starting again at a root server.
        address, _ = self._resolve(self._normalise_name(ns_name), path, depth + 1)
        return address

    def _resolve(self, name, path, depth):
        """Internal iterative walk. Nested no-glue walks share the same path."""
        if depth >= self.max_depth:
            raise RuntimeError("maximum resolution depth reached (possible loop)")

        qname = dns.name.from_text(self._normalise_name(name))
        current_servers = list(ROOT_SERVERS)
        cname_seen = set()

        while True:
            if depth >= self.max_depth:
                raise RuntimeError("maximum resolution depth reached (possible loop)")

            last_error = None
            response = None
            answered_by = None

            # R4: if a server does not answer, try another server.
            for server in current_servers:
                try:
                    response = self._query(server, qname.to_text(), path)
                    answered_by = server
                    break
                except Exception as exc:
                    last_error = exc
                    continue

            if response is None:
                raise RuntimeError(
                    f"no DNS server answered for {qname.to_text()}: {last_error}"
                )

            # 1) Direct A answer.
            address = self._answer_a(response, qname)
            if address is not None:
                return address, path

            # 2) CNAME: restart the walk for the target name.
            cname = self._answer_cname(response, qname)
            if cname is not None:
                target = self._normalise_name(cname)
                if target in cname_seen or target == qname.to_text():
                    raise RuntimeError("CNAME loop detected")
                cname_seen.add(qname.to_text())
                qname = dns.name.from_text(target)
                current_servers = list(ROOT_SERVERS)
                depth += 1
                continue

            # 3) Referral: follow the NS delegation.
            ns_names = self._referral(response)
            if ns_names:
                glue = self._glue_addresses(response, ns_names)
                next_servers = []

                # Prefer glue supplied by the referring server.
                for ns_name in ns_names:
                    next_servers.extend(glue.get(self._normalise_name(ns_name), []))

                # No glue for a nameserver: resolve that nameserver's hostname first.
                if not next_servers:
                    for ns_name in ns_names:
                        try:
                            next_servers.append(
                                self._resolve_ns_name(ns_name, path, depth + 1)
                            )
                        except Exception:
                            continue

                if next_servers:
                    current_servers = list(dict.fromkeys(next_servers))
                    depth += 1
                    continue

            # 4) No usable answer/delegation.
            rcode = response.rcode()
            raise RuntimeError(
                f"server {answered_by} returned no A answer/delegation for {qname.to_text()} "
                f"(rcode={rcode})"
            )

    def resolve(self, name):
        """Return (IPv4 address, path of DNS servers asked in order)."""
        path = []
        address, _ = self._resolve(name, path, 0)
        return address, path


# ------------------------------------------------------------------- harness

def dig_answer(name):
    """What the system resolver says, for comparison."""
    out = subprocess.run(
        ["dig", "+short", name, "A"],
        capture_output=True,
        text=True,
    ).stdout
    return [l for l in out.split() if l and l[0].isdigit()]


def verify():
    r, failures = Resolver(), 0
    for name, kind in VERIFY_NAMES:
        try:
            addr, path = r.resolve(name)
        except NotImplementedError:
            print("Nothing implemented yet - write Resolver.resolve first.")
            return 1
        except Exception as e:
            print(f"  FAIL  {name:<22} your resolver raised {e!r}")
            failures += 1
            continue
        expected = dig_answer(name)
        if addr in expected:
            note = ""
        elif kind == "cdn":
            note = "  <- differs, but this name is CDN-hosted. Explain it."
        else:
            note = "  <- should have matched"
            failures += 1
        print(
            f"  {'FAIL' if note.endswith('matched') else 'ok  '}  {name:<22} "
            f"you={addr:<16} dig={','.join(expected) or '-'}   "
            f"hops={len(path)}{note}"
        )
    print(f"\n  {len(VERIFY_NAMES) - failures}/{len(VERIFY_NAMES)} ok")
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name", nargs="?", default="www.korea.ac.kr")
    p.add_argument("--verify", action="store_true")
    a = p.parse_args()

    if a.verify:
        sys.exit(verify())

    addr, path = Resolver().resolve(a.name)
    for i, server in enumerate(path, 1):
        print(f"  {i}. asked {server}")
    print(f"\n  {a.name} -> {addr}")


if __name__ == "__main__":
    main()
