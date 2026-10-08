#!/usr/bin/env python3
"""Week 6 · Task 1 — Link state: build the forwarding table yourself.

Textbook §5.2 (link state and distance vector) and §5.3 (OSPF).

Every OSPF router ends up holding the same map of the network, and then each one
computes, alone, where to send a packet for every destination. The computation is
Dijkstra; the output is a forwarding table with **one next hop per destination**,
not a path.

That last part is what makes routing work without anybody carrying a route around
in the packet. Build it.

    python3 task1_linkstate.py --verify
"""
import argparse
import heapq

# Undirected weighted graph: node -> {neighbour: cost}
TOPOLOGY = {
    "u": {"v": 2, "w": 5, "x": 1},
    "v": {"u": 2, "w": 3, "x": 2},
    "w": {"u": 5, "v": 3, "x": 3, "y": 1, "z": 5},
    "x": {"u": 1, "v": 2, "w": 3, "y": 1},
    "y": {"w": 1, "x": 1, "z": 2},
    "z": {"w": 5, "y": 2},
}


def _spf(graph, source):
    """Dijkstra 한 번으로 (비용, 첫 홉 집합)을 같이 구한다.

    dist[n] = source에서 n까지 최소 비용
    hops[n] = n으로 가는 최단 경로들이 source를 떠날 때 쓰는 이웃의 집합
              (최단 경로가 하나면 원소 1개, 동률이면 여러 개 = ECMP)

    경로 전체를 저장하지 않는다. 첫 홉은 경로를 따라 그대로 물려받기 때문에
    "n까지의 첫 홉 = n 직전 노드의 첫 홉"만 기억하면 된다.
    """
    dist = {source: 0}
    hops = {source: set()}
    done = set()                      # 비용이 확정된 노드
    heap = [(0, source)]
    while heap:
        d, n = heapq.heappop(heap)
        if n in done:                 # 이미 더 짧은 비용으로 확정된 노드의 낡은 항목
            continue
        done.add(n)
        for m, w in graph.get(n, {}).items():
            nd = d + w
            # source의 이웃이면 첫 홉은 그 이웃 자신, 아니면 n의 첫 홉을 물려받는다
            via = {m} if n == source else hops[n]
            if m not in dist or nd < dist[m]:     # 더 짧은 경로 발견 → 첫 홉 교체
                dist[m] = nd
                hops[m] = set(via)
                heapq.heappush(heap, (nd, m))
            elif nd == dist[m] and m not in done:  # 같은 비용 → 첫 홉 후보 추가
                hops[m] |= via
    # 비용이 양수이므로 n이 꺼내질 때 n으로 오는 동률 경로는 모두 반영돼 있다
    return dist, hops


def dijkstra(graph, source):
    """Shortest path cost from `source` to every node.

    Return {node: cost}. Unreachable nodes must not appear.

    You write the loop. `heapq` is allowed; `networkx` is not.
    """
    dist, _ = _spf(graph, source)
    return {n: c for n, c in dist.items() if n != source}   # 자기 자신은 제외


def ecmp_table(graph, source):
    """동률 경로를 하나로 줄이지 않은 표: {destination: [가능한 첫 홉, ...]}.

    실제 OSPF(RFC 2328 §16.1)는 비용이 같은 첫 홉을 전부 설치하고(ECMP),
    흐름(5-tuple 해시)마다 하나를 골라 트래픽을 나눈다.
    """
    dist, hops = _spf(graph, source)
    return {d: sorted(hops[d]) for d in dist if d != source}


def forwarding_table(graph, source):
    """What the router at `source` actually installs.

    Return {destination: first_hop}, where first_hop is a **direct neighbour**
    of `source` - the one interface a packet for that destination leaves by.

    The source itself is not in the table. Neither are unreachable nodes.

    The trap: it is easy to compute the full path and then take path[1]. That
    works, but think about what a router does when two shortest paths tie, and
    pick a rule. Say which in observation.md.
    """
    # 동률 규칙: 첫 홉 후보 중 이름이 가장 작은 이웃 하나를 고른다.
    # (실제 라우터라면 router ID/인터페이스 번호 같은 고정 기준에 해당)
    # 항상 같은 이웃을 고르므로 결과가 실행할 때마다 바뀌지 않는다.
    return {d: hs[0] for d, hs in ecmp_table(graph, source).items()}


def show():
    """u의 표를 링크 끊기 전/후로 출력 (observation.md 작성용)."""
    before = ecmp_table(TOPOLOGY, "u")
    cut = link_down(TOPOLOGY, "u", "x")
    after = ecmp_table(cut, "u")
    cost_b, cost_a = dijkstra(TOPOLOGY, "u"), dijkstra(cut, "u")
    t_b, t_a = forwarding_table(TOPOLOGY, "u"), forwarding_table(cut, "u")
    print("  dest | before: cost hop (ECMP) | after u-x cut: cost hop (ECMP) | changed")
    for d in sorted(before):
        mark = "hop changed" if t_b[d] != t_a.get(d) else ""
        print(f"   {d}   |   {cost_b[d]:>2}   {t_b[d]}   {','.join(before[d]):<5} "
              f"|   {cost_a.get(d, '-'):>2}   {t_a.get(d, '-')}   "
              f"{','.join(after.get(d, [])):<5} | {mark}")


def link_down(graph, a, b):
    """A copy of `graph` with the link a-b removed, in both directions."""
    g = {n: dict(e) for n, e in graph.items()}
    g[a].pop(b, None)
    g[b].pop(a, None)
    return g


# ------------------------------------------------------------------- harness
# Costs from the textbook's worked example, §5.2.1
EXPECTED_COST_U = {"v": 2, "w": 3, "x": 1, "y": 2, "z": 4}
EXPECTED_TABLE_U = {"v": "v", "w": "x", "x": "x", "y": "x", "z": "x"}


def verify():
    fails = 0
    try:
        cost = dijkstra(TOPOLOGY, "u")
    except NotImplementedError:
        print("  dijkstra is still a stub"); return 1
    ok = cost == EXPECTED_COST_U
    print(f"  {'ok  ' if ok else 'FAIL'}  costs from u: {cost}")
    if not ok:
        print(f"        expected {EXPECTED_COST_U}")
    fails += not ok

    try:
        table = forwarding_table(TOPOLOGY, "u")
    except NotImplementedError:
        print("  forwarding_table is still a stub"); return 1
    ok = table == EXPECTED_TABLE_U
    print(f"  {'ok  ' if ok else 'FAIL'}  table at u:  {table}")
    if not ok:
        print(f"        expected {EXPECTED_TABLE_U}")
    fails += not ok

    # every node should be able to reach every other
    for n in TOPOLOGY:
        t = forwarding_table(TOPOLOGY, n)
        missing = set(TOPOLOGY) - {n} - set(t)
        bad = [d for d, h in t.items() if h not in TOPOLOGY[n]]
        ok = not missing and not bad
        print(f"  {'ok  ' if ok else 'FAIL'}  table at {n} covers all, hops are neighbours"
              + (f"  missing={missing} bad={bad}" if not ok else ""))
        fails += not ok

    # cutting a link must change somebody's mind
    cut = link_down(TOPOLOGY, "u", "x")
    after = forwarding_table(cut, "u")
    ok = after != table
    print(f"  {'ok  ' if ok else 'FAIL'}  u reroutes when u-x goes down: {after}")
    fails += not ok

    print(f"\n  {'all ok' if not fails else str(fails) + ' failed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true")
    p.add_argument("--show", action="store_true",
                   help="u의 표를 u-x 끊기 전/후로 비교")
    a = p.parse_args()
    if a.show:
        raise SystemExit(show())
    raise SystemExit(verify() if a.verify else p.print_help())