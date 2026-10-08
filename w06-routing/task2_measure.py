#!/usr/bin/env python3
"""Week 6 · Task 2 — OSPF 재수렴 시간 측정 (scenario.sh 대신, Windows PowerShell 에서 실행)

scenario.sh 의 cut 은 라우팅 표를 글자 그대로 비교한다. 그런데 FRR 표에는 경로마다
경과 시간(", 00:02:22")이 붙어 있어 매초 글자가 바뀌므로, 실제 재수렴과 상관없이
첫 확인(1초)에서 "바뀌었다"고 판단한다. 또 restore 와 cost 는 시간을 재지 않는다.

이 스크립트는
  * 경과 시간을 지운 표(OSPF 경로 + 이웃 상태)만 비교하고
  * 라우터 3대 각각의 안에서 약 0.05초 간격으로 표를 읽어 (매번 docker exec 를
    거치는 지연 없이) 바뀐 순간을 기록하며
  * 컨테이너 시계(bash 의 EPOCHREALTIME)로 시간을 잰다. 세 컨테이너는 Docker
    Desktop 의 같은 리눅스 커널 시계를 쓰므로 서로 비교할 수 있다.

순서 (저장소 폴더 w06-routing 안에서):
    py task2_measure.py before     수렴된 표 저장           -> out/route-before.txt
    py task2_measure.py cut        r1 eth1 내림 (B3, B4)    -> out/route-after.txt, out/reconverge.txt
    py task2_measure.py restore    r1 eth1 올림 (B5)        -> out/reconverge.txt
    py task2_measure.py cost       r1 eth0 비용 10 -> 100 (C) -> out/cost.txt, out/reconverge.txt
    py task2_measure.py costback   비용 100 -> 10 되돌림     -> out/cost.txt, out/reconverge.txt
    py task2_measure.py silent     (선택) r3 를 얼려 링크 다운 신호 없는 고장 재현
    py task2_measure.py status     지금 상태만 보기
"""
import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # compose.yml 이 있는 저장소 루트
OUT = os.path.join(HERE, "out")
STATE_FILE = os.path.join(OUT, ".measure_state.json")   # 제출하지 않는 작업용 파일

ROUTERS = ["r1", "r2", "r3"]
RID = {"r1": "1.1.1.1", "r2": "2.2.2.2", "r3": "3.3.3.3"}  # topology/rN/frr.conf
CUT_ROUTER, CUT_IF = "r1", "eth1"      # scenario.sh cut 과 같은 인터페이스
COST_ROUTER, COST_IF = "r1", "eth0"    # scenario.sh cost 와 같은 인터페이스
SILENT_ROUTER = "r3"

# 각 라우터 안에서 도는 감시 스크립트. 표가 바뀔 때만 "@@ <시각>" 과 함께 출력한다.
WATCH_SH = r'''
snap() {
  vtysh -c "show ip route ospf" -c "show ip ospf neighbor" </dev/null 2>/dev/null \
  | grep -E '^O|via|^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ' \
  | sed -E 's/, [0-9]+:[0-9]+:[0-9]+$//; s/, [0-9]+d[0-9]+h[0-9]+m$//; s/, [0-9]+w[0-9]+d[0-9]+h$//' \
  | awk '/^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ / {print "NBR", $1, $3, $7; next} {print}'
}
now() {
  if [ -n "${EPOCHREALTIME:-}" ]; then printf '%s' "$EPOCHREALTIME"; else date +%s.%N; fi
}
main() {
  stop=/tmp/w06-stop
  rm -f "$stop"
  max=${1:-120}
  prev="__none__"
  n=0
  tf=$(now)
  SECONDS=0
  while :; do
    ts=$(now)
    cur=$(snap)
    te=$(now)
    n=$((n + 1))
    # 표를 다 읽은 시각(te)을 붙인다. 읽는 도중에 바뀐 내용이 들어올 수 있으므로
    # 시작 시각(ts)을 붙이면 '바뀐 뒤 상태'가 동작 전 시각으로 기록될 수 있다.
    if [ "$cur" != "$prev" ]; then
      printf '@@ %s %s\n%s\n@@end\n' "$te" "$ts" "$cur"
      prev=$cur
    fi
    [ -e "$stop" ] && break
    [ "$SECONDS" -ge "$max" ] && break
    sleep 0.05 2>/dev/null
  done
  printf '@@done %s %s %s\n' "$(now)" "$n" "$tf"
}
main "$@"
'''


# ------------------------------------------------------------------ docker
class DockerError(RuntimeError):
    pass


def _run(args, stdin_text=None, timeout=60, cwd=None):
    try:
        p = subprocess.run(args, input=stdin_text.encode() if stdin_text else None,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        raise DockerError("docker 명령을 찾을 수 없습니다. Docker Desktop 이 설치·실행 중인지 확인하세요.")
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise DockerError(f"{' '.join(args[:4])} ... 실패: {err}")
    return p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


_CID = {}


def cid(r):
    if r not in _CID:
        out = _run(["docker", "compose", "--profile", "routing", "ps", "-q", r], cwd=ROOT).strip()
        if not out:
            raise DockerError(f"{r} 컨테이너가 없습니다. 먼저 "
                              "'docker compose --profile routing up -d r1 r2 r3' 를 실행하세요.")
        _CID[r] = out.splitlines()[0]
    return _CID[r]


def sh(r, script, timeout=30):
    """컨테이너 r 안에서 bash 스크립트 실행 (stdin 으로 넘겨서 따옴표 문제를 피함)."""
    return _run(["docker", "exec", "-i", cid(r), "bash", "-s"], stdin_text=script, timeout=timeout)


def vtysh(r, *cmds):
    args = ["docker", "exec", cid(r), "vtysh"]
    for c in cmds:
        args += ["-c", c]
    return _run(args)


def ctime(r):
    """컨테이너 시계 (초)."""
    return float(sh(r, 'if [ -n "${EPOCHREALTIME:-}" ]; then printf "%s" "$EPOCHREALTIME"; '
                       'else date +%s.%N; fi').strip())


def link_up(r, ifname):
    out = _run(["docker", "exec", cid(r), "ip", "-o", "link", "show", ifname])
    m = re.search(r"<([^>]*)>", out)
    return bool(m) and "UP" in m.group(1).split(",")


def interfaces(r):
    """{ifname: 'a.b.c.d/len'}"""
    out = _run(["docker", "exec", cid(r), "ip", "-o", "-4", "addr", "show"])
    res = {}
    for line in out.splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\S+)", line)
        if m and m.group(1) != "lo":
            res[m.group(1).split("@")[0]] = m.group(2)
    return res


def link_map():
    """{'172.20.0.0/16': [('r1','eth1','172.20.0.3'), ('r3','eth1','172.20.0.2')], ...}"""
    nets = {}
    for r in ROUTERS:
        for ifn, cidr in interfaces(r).items():
            iface = ipaddress.ip_interface(cidr)
            nets.setdefault(str(iface.network), []).append((r, ifn, str(iface.ip)))
    return nets


def link_label(nets, r, ifn):
    for net, ends in nets.items():
        if any(e[0] == r and e[1] == ifn for e in ends):
            names = "-".join(sorted(e[0] for e in ends))
            return f"{names} 링크 ({net})", [e[0] for e in ends if e[0] != r]
    return f"{r} {ifn}", []


def ospf_timers(r, ifn):
    out = vtysh(r, f"show ip ospf interface {ifn}")
    m = re.search(r"Hello (\d+)s, Dead (\d+)s, Wait (\d+)s", out)
    return {"hello": int(m.group(1)), "dead": int(m.group(2)), "wait": int(m.group(3))} if m else None


def raw_tables(r):
    """제출 파일에 넣을 원본 표 (맨 앞의 Codes 설명은 뺌)."""
    def clean(text):
        lines = [l for l in text.splitlines()
                 if not l.startswith("% Can't open") and not l.startswith("Configuration file[")]
        if any(l.startswith("Codes:") for l in lines):
            i = next(i for i, l in enumerate(lines) if l.startswith("Codes:"))
            j = i
            while j < len(lines) and lines[j].strip():
                j += 1
            lines = lines[:i] + lines[j:]
        return "\n".join(lines).strip("\n")

    route = clean(vtysh(r, "show ip route ospf"))
    nbr = clean(vtysh(r, "show ip ospf neighbor"))
    return f"--- show ip route ospf\n{route}\n--- show ip ospf neighbor\n{nbr}"


# ------------------------------------------------------------------ parsing
ROUTE_HEAD = re.compile(r"^(O\S*)\s+(\S+)\s+\[(\d+)/(\d+)\]\s+(.*)$")
ROUTE_CONT = re.compile(r"^\s+\S*\s*(via\s+.*)$")


def _nh(text):
    return re.sub(r",\s*weight \d+", "", text).strip()


def parse_snapshot(lines):
    """감시 출력 한 덩어리 -> (경로 dict, 이웃 dict)"""
    routes, nbrs, cur = {}, {}, None
    for line in lines:
        if line.startswith("NBR "):
            parts = line.split()
            if len(parts) >= 3:
                nbrs[parts[1]] = parts[2]
            continue
        m = ROUTE_HEAD.match(line)
        if m:
            flags, pfx, _ad, metric, rest = m.groups()
            cur = pfx
            routes[pfx] = {"flags": flags, "metric": int(metric), "nh": {_nh(rest)}}
            continue
        m = ROUTE_CONT.match(line)
        if m and cur:
            routes[cur]["nh"].add(_nh(m.group(1)))
    return routes, nbrs


def route_key(routes):
    return tuple(sorted((p, v["flags"], v["metric"], tuple(sorted(v["nh"])))
                        for p, v in routes.items()))


def route_summary(routes):
    out = []
    for p in sorted(routes):
        v = routes[p]
        hops = " / ".join(sorted(v["nh"]))
        out.append(f"{v['flags']:<4}{p:<16} 비용 {v['metric']:<4} {hops}")
    return out


def current_snapshot(r):
    """감시 스크립트와 같은 방식으로 한 번 읽기."""
    script = WATCH_SH.replace('main "$@"', 'snap')
    return parse_snapshot(sh(r, script).splitlines())


# ------------------------------------------------------------------ watchers
class Watcher:
    def __init__(self, r, max_s=150):
        self.r = r
        self.events = []          # [{'t': 컨테이너 시각, 'h': 받은 호스트 시각, 'routes', 'nbrs'}]
        self.lock = threading.Lock()
        self.done = False
        self.period = None
        self.p = subprocess.Popen(["docker", "exec", "-i", cid(r), "bash", "-s", "--", str(max_s)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL)
        self.p.stdin.write(WATCH_SH.encode())
        self.p.stdin.close()
        self.th = threading.Thread(target=self._read, daemon=True)
        self.th.start()

    def _read(self):
        block, t, ts = None, None, None
        for raw in iter(self.p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("@@done"):
                parts = line.split()
                try:   # 표를 몇 번 읽었는지 -> 평균 읽기 간격 (측정 해상도)
                    end, n, first = float(parts[1]), int(parts[2]), float(parts[3])
                    self.period = (end - first) / n if n else None
                except (IndexError, ValueError):
                    pass
                break
            if line.startswith("@@end"):
                routes, nbrs = parse_snapshot(block or [])
                with self.lock:
                    self.events.append({"t": t, "ts": ts, "h": time.time(), "routes": routes,
                                        "nbrs": nbrs, "rk": route_key(routes)})
                block = None
            elif line.startswith("@@ "):
                parts = line[3:].replace(",", ".").split()
                t = float(parts[0])                    # 다 읽은 시각
                ts = float(parts[1]) if len(parts) > 1 else t
                block = []
            elif block is not None:
                block.append(line)
        self.done = True

    def snapshot(self):
        with self.lock:
            return list(self.events)

    def latest(self):
        with self.lock:
            return self.events[-1] if self.events else None

    def stop(self):
        try:
            _run(["docker", "exec", cid(self.r), "touch", "/tmp/w06-stop"], timeout=15)
        except Exception:
            pass
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.th.join(timeout=5)


def start_watchers(routers):
    ws = {r: Watcher(r) for r in routers}
    t_end = time.time() + 20
    while time.time() < t_end and not all(w.latest() for w in ws.values()):
        time.sleep(0.1)
    missing = [r for r, w in ws.items() if not w.latest()]
    if missing:
        stop_watchers(ws)
        raise DockerError(f"{missing} 안에서 감시를 시작하지 못했습니다 (bash/vtysh 확인 필요).")
    return ws


def stop_watchers(ws):
    for w in ws.values():
        w.stop()


def last_route_change_h(ws):
    """마지막으로 어느 라우터든 경로가 바뀐 호스트 시각."""
    h = 0.0
    for w in ws.values():
        ev = w.snapshot()
        for a, b in zip(ev, ev[1:]):
            if a["rk"] != b["rk"]:
                h = max(h, b["h"])
    return h


def wait_until(cond, max_s, progress=None, every=5):
    t_start = time.time()
    next_print = t_start + every
    while time.time() - t_start < max_s:
        if cond():
            return True
        if progress and time.time() >= next_print:
            print(f"    … {time.time() - t_start:4.0f}초 경과  {progress()}")
            next_print += every
        time.sleep(0.2)
    return False


# ------------------------------------------------------------------ analysis
def _before(events, t0):
    prev = [e for e in events if e["t"] < t0]
    return prev[-1] if prev else (events[0] if events else None)


def route_changes(events, t0):
    """t0 이후 경로가 바뀐 시각들 (t0 기준 초)."""
    out, prev = [], _before(events, t0)
    for e in events:
        if e["t"] < t0:
            continue
        if prev is not None and e["rk"] != prev["rk"]:
            out.append(e["t"] - t0)
        prev = e
    return out


def first_time(events, t0, pred):
    """t0 이후 처음 pred 가 참이 된 시각. 감시는 바뀔 때만 기록하므로 t0 직전 상태도 본다."""
    pre = [e for e in events if e["t"] < t0]
    if pre and pred(pre[-1]):
        return 0.0
    for e in events:
        if e["t"] >= t0 and pred(e):
            return e["t"] - t0
    return None


def periods(ws):
    """라우터별 평균 표 읽기 간격 (초)."""
    return {r: (round(w.period, 3) if w.period else None) for r, w in ws.items()}


def period_note(p):
    if not p:
        return None
    vals = [v for v in p.values() if v]
    if not vals:
        return None
    return (f"  (해상도: 각 라우터가 표를 평균 {min(vals):.2f}~{max(vals):.2f}초마다 다시 읽음 -> "
            "시간은 그만큼 늦게 잡힐 수 있음)")


def fmt(x):
    return "-" if x is None else f"{x:.2f}"


# ------------------------------------------------------------------ state / files
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(OUT, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def write(name, text):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text.rstrip("\n") + "\n")


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def all_full(snaps):
    return all(len(n) == 2 and all(s.startswith("Full") for s in n.values())
               for _r, n in snaps.values())


def tables_block(title, raws):
    lines = [title, ""]
    for r in ROUTERS:
        lines += [f"===== {r} (router-id {RID[r]}) =====", raws[r], ""]
    return "\n".join(lines)


def write_reconverge(st):
    L = ["# OSPF 재수렴 측정 - task2_measure.py",
         "# FRR 9.1.0 라우터 3대 (삼각형), 모든 링크 OSPF 비용 10",
         "# 시간: 동작 직전(컨테이너 시계)부터, 각 라우터 안에서 표를 계속 다시 읽어",
         "#       경과 시간을 지운 내용이 바뀐 표를 다 읽은 시점까지"]
    tm = st.get("timers")
    if tm:
        L.append(f"# OSPF 타이머 ({CUT_ROUTER} {CUT_IF}): Hello {tm['hello']}s, Dead {tm['dead']}s, "
                 f"Wait {tm['wait']}s  (frr.conf 에 설정이 없어 기본값)")
    if st.get("links"):
        L.append("# 링크: " + ", ".join(st["links"]))
    L.append("")

    c = st.get("cut")
    if c:
        L += [f"[B4 링크 끊기] {c['when']}  {CUT_ROUTER} {CUT_IF} down = {c['link']}",
              f"재수렴 감지: {fmt(c['converged'])} 초  (끊은 순간 -> 세 라우터 경로가 최종 상태가 될 때까지)"]
        for r in ROUTERS:
            L.append(f"  {r} 경로 변경: 처음 +{fmt(c['first'][r])} s, 마지막 +{fmt(c['last'][r])} s")
        L.append(f"  {CUT_ROUTER} 의 이웃 목록에서 {c['far_rid']} 사라짐: +{fmt(c['near_drop'])} s"
                 "   (자기 인터페이스가 내려간 것을 바로 앎)")
        L.append(f"  {c['far']} 의 이웃 목록에서 {RID[CUT_ROUTER]} 사라짐: +{fmt(c['far_drop'])} s"
                 "   (링크 다운 신호가 없어 hello 가 끊긴 뒤 dead interval 만료로 앎)")
        if period_note(c.get("period")):
            L.append(period_note(c.get("period")))
        L.append("")

    r_ = st.get("restore")
    if r_:
        L += [f"[B5 링크 복구] {r_['when']}  {CUT_ROUTER} {CUT_IF} up",
              f"복구 완료: {fmt(r_['converged'])} 초  (올린 순간 -> 세 라우터 경로가 끊기 전과 같아질 때까지)"]
        for r in ROUTERS:
            L.append(f"  {r} 경로가 끊기 전과 같아짐: +{fmt(r_['restored'][r])} s")
        L.append(f"  {CUT_ROUTER} 의 이웃 목록에 {r_['far_rid']} 처음 나타남(hello 수신): +{fmt(r_['near_seen'])} s")
        L.append(f"  {CUT_ROUTER}-{r_['far']} 인접 Full ({CUT_ROUTER} 쪽): +{fmt(r_['near_full'])} s, "
                 f"({r_['far']} 쪽): +{fmt(r_['far_full'])} s")
        if period_note(r_.get("period")):
            L.append(period_note(r_.get("period")))
        L.append("")

    for key, title in (("cost", "C 비용 변경"), ("costback", "C 비용 되돌림")):
        k = st.get(key)
        if k:
            L += [f"[{title}] {k['when']}  {COST_ROUTER} {COST_IF} ({k['link']}) ip ospf cost "
                  f"{k['from']} -> {k['to']}",
                  f"경로 이동 완료: {fmt(k['converged'])} 초  (설정 순간 -> 마지막 경로 변경)"]
            for r in ROUTERS:
                ch = k["first"][r]
                L.append(f"  {r} 경로 변경: " + ("변화 없음" if ch is None else
                                               f"처음 +{fmt(ch)} s, 마지막 +{fmt(k['last'][r])} s"))
            if period_note(k.get("period")):
                L.append(period_note(k.get("period")))
            L.append("")

    s = st.get("silent")
    if s:
        L += [f"[참고: 조용한 고장] {s['when']}  {SILENT_ROUTER} 컨테이너 일시정지 (docker pause)"
              " - 인터페이스는 살아 있고 hello 만 끊김",
              f"고장 감지·재수렴: {fmt(s['converged'])} 초"]
        for r in s["drop"]:
            L.append(f"  {r} 의 이웃 목록에서 {RID[SILENT_ROUTER]} 사라짐: +{fmt(s['drop'][r])} s, "
                     f"경로 변경 +{fmt(s['change'][r])} s")
        L.append(f"  재개(docker unpause) 후 세 라우터 경로가 원래대로: +{fmt(s['back'])} s")
        if period_note(s.get("period")):
            L.append(period_note(s.get("period")))
        L.append("")
    write("reconverge.txt", "\n".join(L))


def write_cost(st):
    parts = []
    for key in ("cost", "costback"):
        k = st.get(key)
        if not k:
            continue
        parts.append(f"########## {COST_ROUTER} {COST_IF} ({k['link']}) ip ospf cost {k['from']} -> "
                     f"{k['to']}  ({k['when']}, 경로 이동 {fmt(k['converged'])} 초)")
        parts.append(tables_block("##### 바꾸기 전", k["raw_before"]))
        parts.append(tables_block("##### 바꾼 뒤", k["raw_after"]))
    write("cost.txt", "\n".join(parts))


# ------------------------------------------------------------------ modes
def check_converged():
    snaps = {r: current_snapshot(r) for r in ROUTERS}
    if not all_full(snaps):
        for r, (_ro, n) in snaps.items():
            print(f"  {r} 이웃: {n or '없음'}")
        print("\n  아직 OSPF 이웃이 모두 Full 이 아닙니다. 30초쯤 기다렸다가 다시 실행하세요.")
        return None
    return snaps


def mode_status(st):
    base = st.get("baseline")
    for r in ROUTERS:
        routes, nbrs = current_snapshot(r)
        note = ""
        if base:
            note = "  (끊기 전과 같음)" if route_key(routes) == baseline_key(st, r) else "  (끊기 전과 다름)"
        print(f"===== {r}  이웃 {nbrs}{note}")
        for line in route_summary(routes):
            print("   " + line)
    print(f"\n  {CUT_ROUTER} {CUT_IF}: {'UP' if link_up(CUT_ROUTER, CUT_IF) else 'DOWN'}")


def _tuplify(x):
    if isinstance(x, list):
        return tuple(_tuplify(i) for i in x)
    return x


def baseline_key(st, r):
    return _tuplify(st["baseline"][r])


def mode_before(st):
    if not link_up(CUT_ROUTER, CUT_IF):
        print(f"  {CUT_ROUTER} {CUT_IF} 인터페이스가 내려가 있습니다. 먼저 'py task2_measure.py restore' 를 하세요.")
        return 1
    snaps = check_converged()
    if not snaps:
        return 1
    nets = link_map()
    links = []
    for net, ends in sorted(nets.items()):
        links.append(f"{net} = " + "-".join(f"{r}({i} {ip})" for r, i, ip in sorted(ends)))
    st["links"] = links
    st["timers"] = ospf_timers(CUT_ROUTER, CUT_IF)
    st["baseline"] = {r: [list(x) for x in route_key(snaps[r][0])] for r in ROUTERS}
    raws = {r: raw_tables(r) for r in ROUTERS}
    head = [f"# route-before.txt - OSPF 수렴 후, 링크를 끊기 전 ({stamp()})",
            "# 링크: " + ", ".join(links),
            "# O>* 줄 = 직접 연결되지 않은 네트워크를 OSPF 로 배운 경로 (B2)"]
    write("route-before.txt", tables_block("\n".join(head), raws))
    save_state(st)
    for r in ROUTERS:
        print(f"===== {r}  이웃 {snaps[r][1]}")
        for line in route_summary(snaps[r][0]):
            print("   " + line)
    print("\n  링크:\n    " + "\n    ".join(links))
    if st["timers"]:
        print(f"  OSPF 타이머: Hello {st['timers']['hello']}s, Dead {st['timers']['dead']}s")
    print("\n  -> out/route-before.txt 저장. 다음: py task2_measure.py cut")
    return 0


def mode_cut(st):
    if "baseline" not in st:
        print("  먼저 'py task2_measure.py before' 를 실행하세요.")
        return 1
    if not link_up(CUT_ROUTER, CUT_IF):
        print(f"  {CUT_ROUTER} {CUT_IF} 인터페이스가 이미 내려가 있습니다. 'restore' 후 다시 하세요.")
        return 1
    if not check_converged():
        return 1
    nets = link_map()
    label, others = link_label(nets, CUT_ROUTER, CUT_IF)
    far = others[0] if others else "r3"
    far_rid, near_rid = RID[far], RID[CUT_ROUTER]
    print(f"  {CUT_ROUTER} {CUT_IF} = {label} 을(를) 끊습니다. 반대편 {far} 쪽 인터페이스는 살아 있어 링크 다운 신호를 못 받습니다.")
    ws = start_watchers(ROUTERS)
    try:
        time.sleep(1.0)
        t0 = float(sh(CUT_ROUTER, f'printf "%s" "$EPOCHREALTIME"; ip link set {CUT_IF} down').strip())
        print(f"  끊었습니다. {far} 의 이웃 목록에서 {near_rid} 항목이 사라질 때까지 기다립니다 (최대 80초).")
        far_drop_h = [None]

        def cond():
            ev = ws[far].latest()
            if ev and near_rid not in ev["nbrs"] and far_drop_h[0] is None:
                far_drop_h[0] = time.time()
            if far_drop_h[0] is None:
                return False
            return time.time() - max(far_drop_h[0], last_route_change_h(ws)) > 3

        def progress():
            ev = ws[far].latest()
            st_ = ev["nbrs"].get(near_rid, "없음") if ev else "?"
            r1c = route_changes(ws[CUT_ROUTER].snapshot(), t0)
            return (f"{CUT_ROUTER} 경로 {'바뀜 +' + fmt(r1c[0]) + 's' if r1c else '그대로'} | "
                    f"{far} 에서 본 {near_rid}: {st_}")

        ok = wait_until(cond, 80, progress)
    finally:
        stop_watchers(ws)
    if not ok:
        print("  80초 안에 끝나지 않았습니다. 지금까지 기록으로 저장합니다.")
    ev = {r: ws[r].snapshot() for r in ROUTERS}
    first = {r: (route_changes(ev[r], t0) or [None])[0] for r in ROUTERS}
    last = {r: (route_changes(ev[r], t0) or [None])[-1] for r in ROUTERS}
    near_drop = first_time(ev[CUT_ROUTER], t0, lambda e: far_rid not in e["nbrs"])
    far_drop = first_time(ev[far], t0, lambda e: near_rid not in e["nbrs"])
    conv = max([x for x in last.values() if x is not None], default=None)
    raws = {r: raw_tables(r) for r in ROUTERS}
    head = [f"# route-after.txt - {CUT_ROUTER} {CUT_IF} down ({label}) 뒤, "
            f"{far} 의 이웃 목록에서 {near_rid} 항목이 dead interval 로 사라진 다음까지 기다린 상태 ({stamp()})",
            f"# 재수렴 {fmt(conv)} 초 (자세한 시간은 reconverge.txt)"]
    write("route-after.txt", tables_block("\n".join(head), raws))
    st["cut"] = {"when": stamp(), "link": label, "far": far, "far_rid": far_rid,
                 "first": first, "last": last, "near_drop": near_drop, "far_drop": far_drop,
                 "converged": conv, "period": periods(ws)}
    save_state(st)
    write_reconverge(st)
    print()
    for r in ROUTERS:
        print(f"  {r} 경로 변경: 처음 +{fmt(first[r])} s, 마지막 +{fmt(last[r])} s")
    print(f"  {CUT_ROUTER} 의 이웃 목록에서 {far_rid} 사라짐: +{fmt(near_drop)} s")
    print(f"  {far} 의 이웃 목록에서 {near_rid} 사라짐: +{fmt(far_drop)} s")
    print(f"\n  재수렴 {fmt(conv)} 초 -> out/route-after.txt, out/reconverge.txt")
    print("  다음: py task2_measure.py restore")
    return 0


def mode_restore(st):
    if "baseline" not in st:
        print("  먼저 'py task2_measure.py before' 를 실행하세요.")
        return 1
    if link_up(CUT_ROUTER, CUT_IF):
        print(f"  {CUT_ROUTER} {CUT_IF} 인터페이스는 이미 올라가 있습니다. 먼저 cut 을 하세요.")
        return 1
    far = st.get("cut", {}).get("far", "r3")
    far_rid, near_rid = RID[far], RID[CUT_ROUTER]
    ws = start_watchers(ROUTERS)
    try:
        time.sleep(1.0)
        t0 = float(sh(CUT_ROUTER, f'printf "%s" "$EPOCHREALTIME"; ip link set {CUT_IF} up').strip())
        print(f"  {CUT_ROUTER} {CUT_IF} 인터페이스를 올렸습니다. 세 라우터 표가 끊기 전과 같아질 때까지 기다립니다 (최대 90초).")
        done_h = [None]

        def restored():
            for r in ROUTERS:
                e = ws[r].latest()
                if not e or e["rk"] != baseline_key(st, r):
                    return False
            a, b = ws[CUT_ROUTER].latest(), ws[far].latest()
            return (a["nbrs"].get(far_rid, "").startswith("Full")
                    and b["nbrs"].get(near_rid, "").startswith("Full"))

        def cond():
            if restored():
                done_h[0] = done_h[0] or time.time()
                return time.time() - done_h[0] > 2
            done_h[0] = None
            return False

        def progress():
            a = ws[CUT_ROUTER].latest()
            return f"{CUT_ROUTER} 에서 본 {far_rid}: {a['nbrs'].get(far_rid, '없음') if a else '?'}"

        ok = wait_until(cond, 90, progress)
    finally:
        stop_watchers(ws)
    if not ok:
        print("  90초 안에 끊기 전 상태로 돌아오지 않았습니다. 지금까지 기록으로 저장합니다.")
    ev = {r: ws[r].snapshot() for r in ROUTERS}
    rest = {}
    for r in ROUTERS:
        chg = route_changes(ev[r], t0)
        final_ok = ev[r] and ev[r][-1]["rk"] == baseline_key(st, r)
        rest[r] = (chg[-1] if chg else 0.0) if final_ok else None
    near_seen = first_time(ev[CUT_ROUTER], t0, lambda e: far_rid in e["nbrs"])
    near_full = first_time(ev[CUT_ROUTER], t0, lambda e: e["nbrs"].get(far_rid, "").startswith("Full"))
    far_full = first_time(ev[far], t0, lambda e: e["nbrs"].get(near_rid, "").startswith("Full"))
    vals = [x for x in rest.values() if x is not None]
    conv = max(vals) if len(vals) == len(ROUTERS) else None
    st["restore"] = {"when": stamp(), "far": far, "far_rid": far_rid, "restored": rest,
                     "near_seen": near_seen, "near_full": near_full, "far_full": far_full,
                     "converged": conv, "period": periods(ws)}
    save_state(st)
    write_reconverge(st)
    print()
    for r in ROUTERS:
        print(f"  {r} 경로가 끊기 전과 같아짐: +{fmt(rest[r])} s")
    print(f"  {CUT_ROUTER} 이웃 목록에 {far_rid} 처음 나타남: +{fmt(near_seen)} s, Full: +{fmt(near_full)} s")
    print(f"\n  복구 {fmt(conv)} 초 -> out/reconverge.txt")
    print("  다음: py task2_measure.py cost")
    return 0


def mode_cost(st, new_cost, key):
    if "baseline" not in st:
        print("  먼저 'py task2_measure.py before' 를 실행하세요.")
        return 1
    if not link_up(CUT_ROUTER, CUT_IF):
        print(f"  {CUT_ROUTER} {CUT_IF} 인터페이스가 내려가 있습니다. 먼저 restore 를 하세요.")
        return 1
    if not check_converged():
        return 1
    nets = link_map()
    label, _ = link_label(nets, COST_ROUTER, COST_IF)
    m = re.search(r"Cost:\s*(\d+)", vtysh(COST_ROUTER, f"show ip ospf interface {COST_IF}"))
    old = int(m.group(1)) if m else None
    if old == new_cost:
        print(f"  {COST_ROUTER} {COST_IF} 비용이 이미 {new_cost} 입니다.")
        return 1
    raw_before = {r: raw_tables(r) for r in ROUTERS}
    ws = start_watchers(ROUTERS)
    try:
        time.sleep(1.0)
        t0 = float(sh(COST_ROUTER,
                      f'printf "%s" "$EPOCHREALTIME"; vtysh -c "configure terminal" '
                      f'-c "interface {COST_IF}" -c "ip ospf cost {new_cost}" </dev/null >/dev/null 2>&1'
                      ).strip())
        print(f"  {COST_ROUTER} {COST_IF} ({label}) 비용 {old} -> {new_cost}. 경로가 움직이길 기다립니다.")
        h0 = time.time()

        def cond():
            lh = last_route_change_h(ws)
            if lh == 0.0:
                return time.time() - h0 > 10          # 10초 동안 변화 없음
            return time.time() - lh > 3

        wait_until(cond, 30)
    finally:
        stop_watchers(ws)
    ev = {r: ws[r].snapshot() for r in ROUTERS}
    first = {r: (route_changes(ev[r], t0) or [None])[0] for r in ROUTERS}
    last = {r: (route_changes(ev[r], t0) or [None])[-1] for r in ROUTERS}
    conv = max([x for x in last.values() if x is not None], default=None)
    raw_after = {r: raw_tables(r) for r in ROUTERS}
    st[key] = {"when": stamp(), "link": label, "from": old, "to": new_cost, "first": first,
               "last": last, "converged": conv, "raw_before": raw_before, "raw_after": raw_after,
               "period": periods(ws)}
    save_state(st)
    write_cost(st)
    write_reconverge(st)
    print()
    for r in ROUTERS:
        print(f"  {r} 경로 변경: " + ("변화 없음" if first[r] is None else
                                   f"처음 +{fmt(first[r])} s, 마지막 +{fmt(last[r])} s"))
    print(f"\n  경로 이동 {fmt(conv)} 초 -> out/cost.txt, out/reconverge.txt")
    if key == "cost":
        print("  표 비교를 본 뒤 원래대로: py task2_measure.py costback")
    return 0


def mode_silent(st):
    if "baseline" not in st:
        print("  먼저 'py task2_measure.py before' 를 실행하세요.")
        return 1
    if not check_converged():
        return 1
    others = [r for r in ROUTERS if r != SILENT_ROUTER]
    srid = RID[SILENT_ROUTER]
    ws = start_watchers(others)          # r1, r2 는 얼리기 전부터 깨운 뒤까지 계속 감시
    paused = False
    try:
        time.sleep(1.0)
        _run(["docker", "pause", cid(SILENT_ROUTER)])
        paused = True
        t0 = ctime(others[0])
        print(f"  {SILENT_ROUTER} 컨테이너를 얼렸습니다 (인터페이스는 그대로, hello 만 멈춤). "
              f"{', '.join(others)} 쪽에서 알아챌 때까지 기다립니다 (최대 70초).")
        drop_h = [None]

        def cond():
            evs = [ws[r].latest() for r in others]
            if all(e and srid not in e["nbrs"] for e in evs):
                drop_h[0] = drop_h[0] or time.time()
            if drop_h[0] is None:
                return False
            return time.time() - max(drop_h[0], last_route_change_h(ws)) > 3

        def progress():
            return " | ".join(f"{r} 에서 본 {srid}: {ws[r].latest()['nbrs'].get(srid, '없음')}"
                              for r in others)

        wait_until(cond, 70, progress)
        _run(["docker", "unpause", cid(SILENT_ROUTER)])
        paused = False
        t1 = ctime(others[0])
        print(f"  {SILENT_ROUTER} 컨테이너를 다시 깨웠습니다. 원래대로 돌아올 때까지 기다립니다 (최대 90초).")
        ws[SILENT_ROUTER] = Watcher(SILENT_ROUTER)
        ok_h = [None]

        def back():
            for r in ROUTERS:
                e = ws[r].latest()
                if not e or e["t"] < t1 or e["rk"] != baseline_key(st, r) or not (
                        len(e["nbrs"]) == 2 and all(s.startswith("Full") for s in e["nbrs"].values())):
                    ok_h[0] = None
                    return False
            ok_h[0] = ok_h[0] or time.time()
            return time.time() - ok_h[0] > 2

        wait_until(back, 90)
    finally:
        if paused:
            try:
                _run(["docker", "unpause", cid(SILENT_ROUTER)])
            except DockerError:
                pass
        stop_watchers(ws)
    ev = {r: ws[r].snapshot() for r in ROUTERS}
    phase1 = {r: [e for e in ev[r] if e["t"] < t1] for r in others}
    drop = {r: first_time(phase1[r], t0, lambda e: srid not in e["nbrs"]) for r in others}
    change = {r: (route_changes(phase1[r], t0) or [None])[0] for r in others}
    conv_vals = [x for x in change.values() if x is not None]
    conv = max(conv_vals) if conv_vals else None
    bt = []
    for r in ROUTERS:
        if ev[r] and ev[r][-1]["rk"] == baseline_key(st, r):
            chg = route_changes(ev[r], t1)
            bt.append(chg[-1] if chg else 0.0)
        else:
            bt.append(None)
    back_t = None if None in bt else max(bt)
    st["silent"] = {"when": stamp(), "drop": drop, "change": change, "converged": conv,
                    "back": back_t, "period": periods(ws)}
    save_state(st)
    write_reconverge(st)
    print()
    for r in others:
        print(f"  {r} 의 이웃 목록에서 {srid} 사라짐: +{fmt(drop[r])} s, 경로 변경 +{fmt(change[r])} s")
    print(f"  재개 후 원래대로: +{fmt(back_t)} s")
    print("\n  -> out/reconverge.txt 에 [참고: 조용한 고장] 으로 추가")
    return 0


def main():
    p = argparse.ArgumentParser(description="OSPF 재수렴 측정 (6주차 Task 2)")
    p.add_argument("mode", choices=["before", "cut", "restore", "cost", "costback", "silent",
                                    "status", "report"])
    a = p.parse_args()
    st = load_state()
    try:
        if a.mode == "status":
            mode_status(st)
            return 0
        if a.mode == "report":
            write_reconverge(st)
            write_cost(st)
            print("  out/reconverge.txt, out/cost.txt 다시 씀")
            return 0
        return {"before": lambda: mode_before(st),
                "cut": lambda: mode_cut(st),
                "restore": lambda: mode_restore(st),
                "cost": lambda: mode_cost(st, 100, "cost"),
                "costback": lambda: mode_cost(st, 10, "costback"),
                "silent": lambda: mode_silent(st)}[a.mode]()
    except DockerError as e:
        print(f"\n  오류: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n  중단했습니다. 링크 상태는 'py task2_measure.py status' 로 확인하세요.")
        return 1


if __name__ == "__main__":
    sys.exit(main())