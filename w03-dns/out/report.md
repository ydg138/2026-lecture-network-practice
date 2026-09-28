# Week 3 · Task 2 report

## Part A · On the wire

| # | What | Value |
|---|---|---|
| A2 | query / response packet | No. 1 / No. 2 |
| A2 | transaction ID (same on both) | 0xba3d |
| A3 | delegation (answers 0, NS in authority) | No. 4 (kr. server -> korea.ac.kr. : 2 NS in Authority + 2 glue A, answer count 0) |
| A3 | answer (A in answer section) | No. 6 (www.korea.ac.kr A 163.152.6.10, authoritative aa=1) |
| A4 | largest DNS response | 383 bytes |
| A4 | what made it large | No. 2, root server's delegation to kr.: 6 NS records in Authority + 10 glue records (6 A, 4 AAAA) in Additional |

## Part B · Rule (B4)

A site is served by a **third party** if its CNAME chain ends in a zone run by a CDN/cloud-edge operator (Akamai, Fastly, CloudFront, Cloudflare, Azure CDN, ...) that is not the site's own organisation. The zone is the registrable domain (eTLD+1), so `co.uk` / `ac.kr` count as suffixes, not owners.

For comparison the table also shows the naive rule "last two labels differ ⇒ third party".

## Table (chains from network `wifi`)

| site | chain length | final zone | third party? (by hand) | my rule's verdict | naive last-2 | steered? |
|---|---|---|---|---|---|---|
| www.microsoft.com | 2 | akamaiedge.net | yes | third party (Akamai) | third party | yes (4 sets / 6 views) |
| www.netflix.com | 1 | netflix.com | no | own (own zone) | own | no (1 sets / 6 views) |
| www.adobe.com | 2 | akamai.net | yes | third party (Akamai) | third party | yes (4 sets / 6 views) |
| www.cnn.com | 1 | fastly.net | yes | third party (Fastly) | third party | yes (2 sets / 6 views) |
| www.apple.com | 3 | akamaiedge.net | yes | third party (Akamai) | third party | yes (3 sets / 6 views) |
| www.korea.ac.kr | 0 | korea.ac.kr | no | own (own zone) | own | no (1 sets / 6 views) |
| www.stanford.edu | 1 | netlifyglobalcdn.com | yes | own ✗ (other zone (netlifyglobalcdn.com), not a known CDN) | third party | no (1 sets / 6 views) |
| www.bbc.co.uk | 2 | fastly.net | yes | third party (Fastly) | third party | yes (2 sets / 6 views) |
| www.spotify.com | 1 | fastly.net | yes | third party (Fastly) | third party | yes (2 sets / 6 views) |
| www.github.com | 1 | github.com | no | own (own zone) | own | yes (2 sets / 6 views) |
| www.wikipedia.org | 1 | wikimedia.org | no | own (own zone) | third party ✗ | no (1 sets / 6 views) |
| www.nytimes.com | 3 | fastly.net | yes | third party (Fastly) | third party | yes (2 sets / 6 views) |

### CNAME chains

- `www.microsoft.com`: `www.microsoft.com` → `www.microsoft.com-c-3.edgekey.net` → `e13678.dscb.akamaiedge.net`
- `www.netflix.com`: `www.netflix.com` → `www.prod.ftl.netflix.com`
- `www.adobe.com`: `www.adobe.com` → `www.adobe.com.edgesuite.net` → `a1319.dscr.akamai.net`
- `www.cnn.com`: `www.cnn.com` → `cnn-tls.map.fastly.net`
- `www.apple.com`: `www.apple.com` → `www-apple-com.v.aaplimg.com` → `www.apple.com.edgekey.net` → `e6858.dsce9.akamaiedge.net`
- `www.korea.ac.kr`: `www.korea.ac.kr`
- `www.stanford.edu`: `www.stanford.edu` → `stanford.netlifyglobalcdn.com`
- `www.bbc.co.uk`: `www.bbc.co.uk` → `www.bbc.co.uk.pri.bbc.co.uk` → `bbc.map.fastly.net`
- `www.spotify.com`: `www.spotify.com` → `atc.spotify.map.fastly.net`
- `www.github.com`: `www.github.com` → `github.com`
- `www.wikipedia.org`: `www.wikipedia.org` → `dyna.wikimedia.org`
- `www.nytimes.com`: `www.nytimes.com` → `www.prd.map.nytimes.com` → `www.prd.map.nytimes.xovr.nyt.net` → `nytimes.map.fastly.net`

## Steering number (B5)

Networks: `wifi`, `tethering` · resolvers: system, google, quad9

**7 of 7 CDN-hosted sites answered differently to a different resolver or network.**

Steered: www.microsoft.com, www.adobe.com, www.cnn.com, www.apple.com, www.bbc.co.uk, www.spotify.com, www.nytimes.com

## Where a rule gets it wrong (✗ in the table)

- `www.stanford.edu` → `stanford.netlifyglobalcdn.com`: really **third party**, but my rule says **own**.
- `www.wikipedia.org` → `dyna.wikimedia.org`: really **own**, but naive last-2 says **third party**.

Why:

- **Naive last-2 rule** only compares strings. `wikipedia.org` vs `wikimedia.org` differ, but both are the Wikimedia Foundation, so it calls an in-house edge a third party. (It would also treat `co.uk` as an owner.)
- **My rule** is only as good as its operator list. `netlifyglobalcdn.com` is Netlify's CDN, but it is not in `CDN_OPERATORS`, so Stanford is called "own". A CDN it has never heard of is invisible to it.
- **Neither rule** can see an anycast CDN that is reached with no CNAME at all: the chain has length 0 and ends in the site's own zone.
