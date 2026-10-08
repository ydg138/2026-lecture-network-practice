#!/usr/bin/env python3
"""Week 6 · Task 3 — Reconverge without recomputing the world.

Textbook §5.2.1, §5.3.

A link flaps. Every router in the area has to decide what changed. `FullRecompute`
does the honest thing: throw the table away and run Dijkstra again, from scratch,
for every event. It is correct and it is what the first implementations did.

It is also why a single flapping link in a large area used to melt the CPU of
every router that could see it.

Beat it:

    python3 bench.py
    python3 bench.py --yours

Correctness first. `bench.py` compares your table against a full recompute after
**every single event**. A router that is fast and wrong black-holes traffic.
"""
import heapq

# The harness counts how many times you run a full SPF. This is the score:
# wall-clock time in Python says more about dictionary overhead than about
# routing, but "how many times did the CPU have to recompute the world" is
# exactly what melted real routers.
SPF_RUNS = 0


def dijkstra_table(graph, source):
    """Reference shortest-path-first. Returns {destination: first_hop}.

    Use THIS function whenever you need a full recompute. Rolling your own to
    dodge the counter is not an optimisation, it is cheating the meter.
    """
    global SPF_RUNS
    SPF_RUNS += 1
    best = {source: (0, None)}
    pq, done = [(0, source, None)], set()
    while pq:
        cost, node, first_hop = heapq.heappop(pq)
        if node in done:
            continue
        done.add(node)
        best[node] = (cost, first_hop)
        for nbr, w in sorted(graph[node].items()):
            if nbr in done:
                continue
            hop = nbr if node == source else first_hop
            if cost + w < best.get(nbr, (float("inf"), None))[0]:
                best[nbr] = (cost + w, hop)
                heapq.heappush(pq, (cost + w, nbr, hop))
    return {d: h for d, (_, h) in best.items() if d != source and h}


class FullRecompute:
    """On every event, forget everything and run SPF again."""

    def __init__(self, graph, source):
        self.graph = {n: dict(e) for n, e in graph.items()}
        self.source = source
        self.table = dijkstra_table(self.graph, source)

    def link_change(self, a, b, cost):
        """cost=None means the link went down."""
        if cost is None:
            self.graph[a].pop(b, None)
            self.graph[b].pop(a, None)
        else:
            self.graph[a][b] = cost
            self.graph[b][a] = cost
        self.table = dijkstra_table(self.graph, self.source)


class YourRouter:
    """Your router. Same two methods, same table, less work per event.

    What is actually true after one link changes:

      * most destinations are not affected at all
      * a link that is not on any of your shortest paths, going *up*, can only
        matter if it creates something shorter
      * a link going *down* only matters if you were using it

    Deciding which of those applies, cheaply, without getting it wrong, is the
    task. Getting it wrong is worse than being slow - the harness will catch it
    on the event where it happens.
    """

    # 이벤트마다 무엇을 했는지 센다 (observation.md 작성용)
    KINDS = ("no-op", "off-tree down/up", "no shorter path", "tie: reparent",
             "tree cost shift", "shorter, contained",
             "full SPF: tree link lost", "full SPF: shorter path", "full SPF: reachability")

    def __init__(self, graph, source):
        self.graph = {n: dict(e) for n, e in graph.items()}
        self.source = source
        self.stats = {k: 0 for k in self.KINDS}
        self._full_spf()

    # ------------------------------------------------------------ 전체 재계산
    def _full_spf(self):
        """전체 SPF 1회. 표는 반드시 dijkstra_table() 로 만든다 (카운터 +1).

        dijkstra_table() 은 첫 홉만 돌려주므로, 같은 그래프에서 이벤트 사이에
        들고 있을 두 가지를 함께 만든다.
          dist[v]   source 에서 v 까지 최단 거리
          parent[v] dijkstra_table 이 v 의 첫 홉을 물려받은 앞 노드 (SPF 트리)
        이 거리 계산은 언제나 dijkstra_table() 호출과 짝으로만 돌아서, 전체 재계산
        횟수는 카운터에 그대로 1회로 잡힌다. 카운터를 피하려는 용도가 아니다.
        """
        self.table = dijkstra_table(self.graph, self.source)
        g, s = self.graph, self.source
        dist, pq, done = {s: 0}, [(0, s)], set()
        while pq:
            c, n = heapq.heappop(pq)
            if n in done:
                continue
            done.add(n)
            for m, w in g[n].items():
                if m not in dist or c + w < dist[m]:
                    dist[m] = c + w
                    heapq.heappush(pq, (c + w, m))
        self.dist = dist
        self.parent, self.children = {}, {n: set() for n in dist}
        for v in dist:
            if v != s:
                p = self._best_parent(v)
                self.parent[v] = p
                self.children[p].add(v)

    def _best_parent(self, v):
        """dijkstra_table 의 동률 규칙 그대로: v 의 최단 경로 앞 노드 중
        (거리, 이름) 이 가장 작은 것. 힙이 (비용, 노드, 첫 홉) 순으로 꺼내므로
        먼저 꺼내진 앞 노드가 v 를 처음 갱신하고, 같은 비용은 다시 갱신하지 않는다."""
        d, best = self.dist, None
        for u, w in self.graph[v].items():
            if u in d and d[u] + w == d[v]:
                if best is None or (d[u], u) < (d[best], best):
                    best = u
        return best

    def _reparent(self, v, p):
        """거리는 그대로이고 v 의 부모만 p 로 바뀔 때: v 의 서브트리 첫 홉만 고친다."""
        old = self.parent[v]
        if old == p:
            return
        self.children[old].discard(v)
        self.children[p].add(v)
        self.parent[v] = p
        hop = v if p == self.source else self.table[p]
        if hop == self.table[v]:
            return                       # 첫 홉이 같으면 표는 그대로
        stack = [v]                      # v 아래는 모두 v 의 첫 홉을 물려받는다
        while stack:
            x = stack.pop()
            self.table[x] = hop
            stack.extend(self.children[x])

    # ------------------------------------------------------------ 이벤트
    def link_change(self, a, b, cost):
        """cost=None means the link went down."""
        old = self.graph[a].get(b)
        if old == cost:                                   # 이미 그 상태
            self.stats["no-op"] += 1
            return
        if cost is None:
            self.graph[a].pop(b, None)
            self.graph[b].pop(a, None)
        else:
            self.graph[a][b] = cost
            self.graph[b][a] = cost

        if cost is None or (old is not None and cost > old):
            self._worse(a, b, old, cost)                  # 끊김 또는 비용 증가
        else:
            self._better(a, b, old, cost)                 # 새 링크 또는 비용 감소

    def _tree_child(self, a, b):
        """a-b 가 SPF 트리 간선이면 아래쪽 노드, 아니면 None."""
        if self.parent.get(b) == a:
            return b
        if self.parent.get(a) == b:
            return a
        return None

    def _subtree(self, v):
        out, stack = [], [v]
        while stack:
            x = stack.pop()
            out.append(x)
            stack.extend(self.children[x])
        return out

    def _shift(self, child, delta):
        """child 까지의 거리가 delta 만큼 바뀔 때 (트리 간선 비용 변경, 또는 새 지름길).

        child 아래 서브트리 S 의 거리가 전부 똑같이 delta 만큼 움직이는지,
        S 와 바깥을 잇는 경계 링크만 보고 판정한다 (SPF 없음).
        가능하면 거리/부모/표를 고치고 True, 아니면 False (전체 SPF 필요).
        """
        S = self._subtree(child)
        inS, d = set(S), self.dist
        if delta > 0:
            # 비싸짐: 바깥에서 S 로 들어오는 길이 '밀린 거리'보다 짧으면 모양이 바뀐다
            for z in S:
                for u, w in self.graph[z].items():
                    if u not in inS and d[u] + w < d[z] + delta:
                        return False
            for z in S:
                d[z] += delta
            # 거리는 정해졌다. 바깥에서 같은 거리로 들어오는 앞 노드가 생겼으면
            # 동률 규칙상 부모가 바뀔 수 있으므로 S 안의 부모와 첫 홉을 다시 정한다.
            for z in sorted(S, key=lambda x: (d[x], x)):
                p = self._best_parent(z)
                if p != self.parent[z]:
                    self.children[self.parent[z]].discard(z)
                    self.children[p].add(z)
                    self.parent[z] = p
                self.table[z] = z if p == self.source else self.table[p]
            return True
        # 싸짐: S 는 그대로 delta 만큼 가까워진다 (바깥 경로는 원래부터 더 길었다).
        # 이제 S 를 거쳐 바깥 노드가 더 짧아지면 모양이 바뀐다.
        ties = set()
        for z in S:
            for y, w in self.graph[z].items():
                if y not in inS:
                    if d[z] + delta + w < d[y]:
                        return False
                    if d[z] + delta + w == d[y]:
                        ties.add(y)
        for z in S:
            d[z] += delta
        # child 의 부모: 트리 간선이 싸진 경우엔 그대로, 새 지름길이면 그 지름길 쪽 노드
        self._reparent(child, self._best_parent(child))
        for y in sorted(ties, key=lambda x: (d[x], x)):   # 거리는 그대로, 부모만 확인
            self._reparent(y, self._best_parent(y))
        return True

    def _worse(self, a, b, old, cost):
        """링크가 끊기거나 비싸짐: 내 SPF 트리의 간선일 때만 의미가 있다."""
        child = self._tree_child(a, b)
        if child is None:
            # 트리 밖 링크. 최단 경로 동률 후보였더라도 부모가 아니었으면
            # dijkstra_table 결과에 영향을 준 적이 없다.
            self.stats["off-tree down/up"] += 1
            return
        # 같은 거리로 child 에 닿는 다른 앞 노드가 있으면 거리는 하나도 안 바뀐다.
        alt = self._best_parent(child)
        if alt is not None:
            self.stats["tie: reparent"] += 1
            self._reparent(child, alt)
            return
        if cost is not None and self._shift(child, cost - old):
            self.stats["tree cost shift"] += 1
            return
        self.stats["full SPF: tree link lost"] += 1
        self._full_spf()

    def _better(self, a, b, old, c):
        """링크가 생기거나 싸짐: 지금 거리보다 더 짧은 길을 만들 때만 의미가 있다.
        SPF 없이 이미 아는 거리로 바로 판정한다."""
        d = self.dist
        child = self._tree_child(a, b) if old is not None else None
        if child is not None:
            if self._shift(child, c - old):
                self.stats["tree cost shift"] += 1
            else:
                self.stats["full SPF: shorter path"] += 1
                self._full_spf()
            return
        if a not in d and b not in d:
            self.stats["off-tree down/up"] += 1           # 둘 다 못 가는 곳
            return
        if a not in d or b not in d:
            self.stats["full SPF: reachability"] += 1     # 못 가던 곳이 열림
            self._full_spf()
            return
        if d[a] + c < d[b] or d[b] + c < d[a]:
            # 지름길: v 가 u 를 거쳐 더 가까워진다. v 의 서브트리 전체가 같은 양만큼
            # 따라오고, 그 이득이 서브트리 밖으로 새지 않으면 SPF 없이 끝난다.
            u, v = (a, b) if d[a] + c < d[b] else (b, a)
            if self._shift(v, d[u] + c - d[v]):
                self.stats["shorter, contained"] += 1
                return
            self.stats["full SPF: shorter path"] += 1
            self._full_spf()
            return
        # 더 짧아지지 않음 -> 모든 거리 그대로. 같은 거리(동률)가 생겼다면
        # 동률 규칙상 부모가 바뀔 수 있으니 그 노드만 확인한다.
        for u, v in ((a, b), (b, a)):
            if v != self.source and d[u] + c == d[v]:
                self.stats["tie: reparent"] += 1
                self._reparent(v, self._best_parent(v))
                return
        self.stats["no shorter path"] += 1


if __name__ == "__main__":
    # 벤치마크와 같은 이벤트를 돌려 이벤트 종류별로 무엇을 했는지 보여준다
    import bench
    graph, events = bench.build()
    r = YourRouter(graph, sorted(graph)[0])
    for ev in events:
        r.link_change(*ev)
    print(f"\n  events {len(events)}, full SPF runs {SPF_RUNS} (including the initial one)\n")
    for k in YourRouter.KINDS:
        print(f"  {k:<28} {r.stats[k]:>4}")
    print()