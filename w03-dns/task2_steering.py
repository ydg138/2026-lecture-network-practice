#!/usr/bin/env python3
"""Week 3 · Task 2 — Does DNS actually steer you? Measure it.

Textbook §2.4.3 (records) and §2.5 (CDNs).

    python3 task2_steering.py --collect --network campus     # 1st network
    python3 task2_steering.py --collect --network tethering  # 2nd network (B3)
    python3 task2_steering.py --report                       # -> out/report.md

Part A numbers (A2/A3/A4) come from Wireshark. Put them in out/partA.json
(see PARTA_TEMPLATE below); --report copies them into report.md.

Transport: dnspython if installed (pip install dnspython), otherwise `dig`.
"""
import argparse, json, os, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
CHAINS = os.path.join(OUT, "chains.json")
PARTA = os.path.join(OUT, "partA.json")
REPORT = os.path.join(OUT, "report.md")

SITES = [
    "www.microsoft.com",     # Akamai, multi-hop
    "www.netflix.com",       # own CDN
    "www.adobe.com",
    "www.cnn.com",
    "www.apple.com",
    "www.korea.ac.kr",       # no CDN at all
    "www.stanford.edu",
    "www.bbc.co.uk",
    "www.spotify.com",
    "www.github.com",
    "www.wikipedia.org",
    "www.nytimes.com",
]

RESOLVERS = {
    "system": None,          # whatever is in your resolv.conf
    "google": "8.8.8.8",
    "quad9":  "9.9.9.9",
}

MAX_HOPS = 10                # CNAME loop guard

PARTA_TEMPLATE = {
    "A2_query_packet": "No. ?",
    "A2_response_packet": "No. ?",
    "A2_transaction_id": "0x????",
    "A3_delegation_packet": "No. ?",
    "A3_answer_packet": "No. ?",
    "A4_largest_response_bytes": "?",
    "A4_why_large": "e.g. many NS records in Authority + glue A/AAAA in Additional",
}

# ---------------------------------------------------------------- transport

try:
    import dns.resolver
    HAVE_DNSPYTHON = True
except ImportError:
    HAVE_DNSPYTHON = False


def _resolver(server):
    r = dns.resolver.Resolver(configure=True)
    if server:
        r.nameservers = [server]
    r.lifetime = 5
    return r


def query(name, rtype, server=None):
    """Return a list of record strings. Transport only - the thinking is yours."""
    if HAVE_DNSPYTHON:
        try:
            ans = _resolver(server).resolve(name, rtype, raise_on_no_answer=False)
        except Exception:
            return []
        if ans.rrset is None:
            return []
        # keep only records that belong to *this* name (not the rest of the chain)
        return [r.to_text().rstrip(".").lower() for r in ans.rrset]
    args = ["dig", "+short", name, rtype]
    if server:
        args.insert(1, f"@{server}")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    lines = [l.strip().rstrip(".").lower() for l in out.splitlines() if l.strip()]
    if rtype == "CNAME":
        return lines[:1]
    # dig +short A prints the CNAME chain first; keep only addresses
    return [l for l in lines if l.replace(".", "").isdigit()]


def follow_chain(name, server=None):
    """[name, cname1, cname2, ...] - every hop, until no more CNAME."""
    chain = [name.lower()]
    for _ in range(MAX_HOPS):
        nxt = query(chain[-1], "CNAME", server)
        if not nxt or nxt[0] in chain:
            break
        chain.append(nxt[0])
    return chain


# ---------------------------------------------------------------- collect

def collect(network):
    data = {}
    if os.path.exists(CHAINS):
        data = json.load(open(CHAINS, encoding="utf-8"))
    data.setdefault("networks", {})
    run = {"collected_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sites": {}}

    for site in SITES:
        chain = follow_chain(site)                      # via system resolver
        per_res = {}
        for label, server in RESOLVERS.items():
            addrs = sorted(set(query(chain[-1], "A", server)))
            per_res[label] = addrs
        run["sites"][site] = {"chain": chain, "answers": per_res}
        print(f"  {site:20s} hops={len(chain)-1}  final={chain[-1]}")
        for label, addrs in per_res.items():
            print(f"      {label:7s} {', '.join(addrs) or '(no answer)'}")

    data["networks"][network] = run
    json.dump(data, open(CHAINS, "w", encoding="utf-8"), indent=2)
    print(f"\n  saved network '{network}' -> {CHAINS}")
    if not os.path.exists(PARTA):
        json.dump(PARTA_TEMPLATE, open(PARTA, "w", encoding="utf-8"), indent=2)
        print(f"  created {PARTA} - fill in your Wireshark packet numbers")


# ---------------------------------------------------------------- the rule

# Public suffixes that are more than one label (enough for this site list).
MULTI_SUFFIX = {"co.uk", "ac.uk", "ac.kr", "co.kr", "or.kr", "go.kr",
                "com.au", "co.jp"}

# Zones that belong to CDN / cloud-edge operators, and who runs them.
CDN_OPERATORS = {
    "akamaiedge.net": "Akamai", "edgekey.net": "Akamai", "edgesuite.net": "Akamai",
    "akamai.net": "Akamai", "akamaized.net": "Akamai", "akadns.net": "Akamai",
    "fastly.net": "Fastly", "fastlylb.net": "Fastly",
    "cloudfront.net": "Amazon CloudFront", "cloudflare.net": "Cloudflare",
    "azureedge.net": "Microsoft Azure CDN", "azurefd.net": "Microsoft Azure Front Door",
    "trafficmanager.net": "Microsoft Azure",
    "edgecastcdn.net": "Edgio", "llnwd.net": "Edgio",
    "cdn77.org": "CDN77", "incapdns.net": "Imperva",
    "pantheonsite.io": "Pantheon", "googlehosted.com": "Google",
}

# Ground truth, judged by hand from the chains (who really runs the edge).
# Used only to check the rule - the rule itself never looks at this.
TRUTH = {
    "www.microsoft.com": True,   # Akamai
    "www.netflix.com":   False,  # own CDN (Open Connect), stays in netflix.com
    "www.adobe.com":     True,   # Akamai
    "www.cnn.com":       True,   # Fastly
    "www.apple.com":     True,   # Akamai (after aaplimg.com, Apple's own zone)
    "www.korea.ac.kr":   False,  # no CNAME, no CDN
    "www.stanford.edu":  True,   # Netlify
    "www.bbc.co.uk":     True,   # Fastly
    "www.spotify.com":   True,   # Fastly
    "www.github.com":    False,  # github.com itself, no CDN in DNS
    "www.wikipedia.org": False,  # wikimedia.org = same organisation
    "www.nytimes.com":   True,   # Fastly
}

# Same organisation, different registrable domain.
SAME_OWNER = {
    "wikipedia.org": {"wikimedia.org"},
    "microsoft.com": {"msedge.net"},
}


def zone(host):
    """Registrable domain (eTLD+1), aware of multi-label suffixes like co.uk."""
    labels = host.rstrip(".").split(".")
    if ".".join(labels[-2:]) in MULTI_SUFFIX:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def naive_last_two(site, final):
    """The rule the task warns about: compare only the last two labels."""
    return site.split(".")[-2:] != final.split(".")[-2:]


def my_rule(site, final):
    """Third party  <=>  the chain ends in a zone run by a CDN operator
    that is not the site's own organisation.

    Returns (verdict: bool, reason: str)."""
    s, f = zone(site), zone(final)
    if f in CDN_OPERATORS:
        return True, CDN_OPERATORS[f]
    if f == s or f in SAME_OWNER.get(s, set()):
        return False, "own zone"
    return False, f"other zone ({f}), not a known CDN"


# ---------------------------------------------------------------- report

def steered(site_runs):
    """Did the address set differ across resolvers and/or networks?"""
    sets = {}
    for net, answers in site_runs:
        for res, addrs in answers.items():
            if addrs:
                sets[f"{net}/{res}"] = frozenset(addrs)
    distinct = set(sets.values())
    return len(distinct) > 1, len(distinct), len(sets)


def report():
    if not os.path.exists(CHAINS):
        raise SystemExit("run --collect first")
    data = json.load(open(CHAINS, encoding="utf-8"))
    nets = data["networks"]
    net_names = list(nets)
    first = nets[net_names[0]]["sites"]
    parta = json.load(open(PARTA, encoding="utf-8")) if os.path.exists(PARTA) else PARTA_TEMPLATE

    L = ["# Week 3 · Task 2 report", ""]

    # ---- Part A
    L += ["## Part A · On the wire", "",
          "| # | What | Value |", "|---|---|---|",
          f"| A2 | query / response packet | {parta['A2_query_packet']} / {parta['A2_response_packet']} |",
          f"| A2 | transaction ID (same on both) | {parta['A2_transaction_id']} |",
          f"| A3 | delegation (answers 0, NS in authority) | {parta['A3_delegation_packet']} |",
          f"| A3 | answer (A in answer section) | {parta['A3_answer_packet']} |",
          f"| A4 | largest DNS response | {parta['A4_largest_response_bytes']} bytes |",
          f"| A4 | what made it large | {parta['A4_why_large']} |", ""]

    # ---- B4 rule
    L += ["## Part B · Rule (B4)", "",
          "A site is served by a **third party** if its CNAME chain ends in a zone "
          "run by a CDN/cloud-edge operator (Akamai, Fastly, CloudFront, Cloudflare, "
          "Azure CDN, ...) that is not the site's own organisation. The zone is the "
          "registrable domain (eTLD+1), so `co.uk` / `ac.kr` count as suffixes, not owners.",
          "", "For comparison the table also shows the naive rule "
          "\"last two labels differ ⇒ third party\".", ""]

    # ---- table
    L += [f"## Table (chains from network `{net_names[0]}`)", "",
          "| site | chain length | final zone | third party? (by hand) | my rule's verdict | naive last-2 | steered? |",
          "|---|---|---|---|---|---|---|"]
    disagreements, cdn_sites, steered_sites = [], [], []
    for site in SITES:
        chain = first[site]["chain"]
        final = chain[-1]
        mine, why = my_rule(site, final)
        naive = naive_last_two(site, final)
        runs = [(n, nets[n]["sites"][site]["answers"]) for n in net_names
                if site in nets[n]["sites"]]
        diff, k, m = steered(runs)
        if mine:
            cdn_sites.append(site)
            if diff:
                steered_sites.append(site)
        truth = TRUTH.get(site)
        if truth is not None and (mine != truth or naive != truth):
            disagreements.append((site, final, truth, mine, naive, why))
        ok = lambda v: "" if truth is None or v == truth else " ✗"
        L.append(f"| {site} | {len(chain) - 1} | {zone(final)} | "
                 f"{'?' if truth is None else ('yes' if truth else 'no')} | "
                 f"{'third party' if mine else 'own'}{ok(mine)} ({why}) | "
                 f"{'third party' if naive else 'own'}{ok(naive)} | "
                 f"{'yes' if diff else 'no'} ({k} sets / {m} views) |")
    L.append("")

    L += ["### CNAME chains", ""]
    for site in SITES:
        L.append(f"- `{site}`: " + " → ".join(f"`{h}`" for h in first[site]["chain"]))
    L.append("")

    # ---- B5
    L += ["## Steering number (B5)", "",
          f"Networks: {', '.join(f'`{n}`' for n in net_names)} · "
          f"resolvers: {', '.join(RESOLVERS)}", "",
          f"**{len(steered_sites)} of {len(cdn_sites)} CDN-hosted sites answered "
          f"differently to a different resolver or network.**", "",
          "Steered: " + (", ".join(steered_sites) or "none"), ""]
    if len(net_names) < 2:
        L += ["> Only one network collected so far - run `--collect --network <name>` "
              "on a second network (B3).", ""]

    # ---- where the rules go wrong
    L += ["## Where a rule gets it wrong (✗ in the table)", ""]
    tp = lambda v: "third party" if v else "own"
    for site, final, truth, mine, naive, why in disagreements:
        wrong = []
        if mine != truth:
            wrong.append(f"my rule says **{tp(mine)}**")
        if naive != truth:
            wrong.append(f"naive last-2 says **{tp(naive)}**")
        L.append(f"- `{site}` → `{final}`: really **{tp(truth)}**, but "
                 + " and ".join(wrong) + ".")
    L += ["", "Why:", "",
          "- **Naive last-2 rule** only compares strings. `wikipedia.org` vs "
          "`wikimedia.org` differ, but both are the Wikimedia Foundation, so it calls "
          "an in-house edge a third party. (It would also treat `co.uk` as an owner.)",
          "- **My rule** is only as good as its operator list. `netlifyglobalcdn.com` "
          "is Netlify's CDN, but it is not in `CDN_OPERATORS`, so Stanford is called "
          "\"own\". A CDN it has never heard of is invisible to it.",
          "- **Neither rule** can see an anycast CDN that is reached with no CNAME at "
          "all: the chain has length 0 and ends in the site's own zone.", ""]

    open(REPORT, "w", encoding="utf-8").write("\n".join(L))
    print(f"  wrote {REPORT}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--collect", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--network", default="network1",
                   help="label for this vantage point, e.g. campus / tethering")
    a = p.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.collect:
        collect(a.network)
    elif a.report:
        report()
    else:
        p.print_help()