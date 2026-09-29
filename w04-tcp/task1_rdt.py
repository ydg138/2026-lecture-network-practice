#!/usr/bin/env python3
"""Week 4 · Task 1 — Build reliable delivery on top of an unreliable channel.

Textbook §3.4 (reliable data transfer) and §3.5 (TCP's sequence numbers).

`UnreliableChannel` below loses packets, reorders them, duplicates them, and
delays them. It is the network as §3.4 models it. Your job is to move a file
across it and have the bytes arrive intact and in order.

That is the whole of TCP's reliability story with the congestion control taken
out, and it is worth building once by hand before you ever trust a socket again.

    python3 task1_rdt.py --verify
"""
import argparse, hashlib, random

PAYLOAD = 8            # bytes per packet - small, so you see the sequencing


class UnreliableChannel:
    """Loses 10%, duplicates 3%, reorders, and delays. Deterministic by seed.

    You may not make it nicer. You may not read its internals. It is the only
    way your sender can reach your receiver.
    """

    def __init__(self, seed=246, loss=0.10, dup=0.03, reorder=0.10):
        self.rng = random.Random(seed)
        self.loss, self.dup, self.reorder = loss, dup, reorder
        self.wire = []          # packets in flight, in no particular order
        self.stats = {"sent": 0, "lost": 0, "duplicated": 0, "delivered": 0}

    def send(self, packet):
        """Hand a packet to the network. It may never come out."""
        self.stats["sent"] += 1
        if self.rng.random() < self.loss:
            self.stats["lost"] += 1
            return
        copies = 2 if self.rng.random() < self.dup else 1
        self.stats["duplicated"] += copies - 1
        for _ in range(copies):
            if self.rng.random() < self.reorder and self.wire:
                self.wire.insert(self.rng.randrange(len(self.wire)), packet)
            else:
                self.wire.append(packet)

    def receive(self):
        """Take the next packet out, or None if the network has nothing."""
        if not self.wire:
            return None
        self.stats["delivered"] += 1
        return self.wire.pop(0)


class Sender:
    """Selective Repeat 송신자.

    - data를 PAYLOAD(8바이트) 조각으로 나누고 0, 1, 2 ... 번호를 붙인다 (R1)
    - 윈도우 안의 조각들을 한꺼번에 보내고, 조각마다 따로 타이머를 둔다
    - ACK가 안 온 채로 TIMEOUT step이 지나면 그 조각만 다시 보낸다 (R4)
    - ACK는 번호를 확인해서 처리한다. 같은 ACK가 두 번 와도 set이라 무해하다 (R3)
    """

    WINDOW = 16      # 한 번에 ACK 없이 보낼 수 있는 최대 조각 수
    TIMEOUT = 10     # 이 step 수만큼 ACK가 없으면 재전송

    def __init__(self, data_channel, ack_channel, data):
        self.data_ch = data_channel            # 데이터를 보내는 채널 (up)
        self.ack_ch = ack_channel              # ACK를 받는 채널 (down)
        self.chunks = [data[i:i + PAYLOAD] for i in range(0, len(data), PAYLOAD)]
        self.acked = set()                     # ACK를 받은 조각 번호들
        self.sent_at = {}                      # 조각 번호 -> 마지막으로 보낸 step
        self.base = 0                          # 아직 ACK 안 된 가장 작은 번호
        self.now = 0                           # 현재 step (시계 대신)
        self.transmissions = 0                 # 관찰용: 총 전송 횟수
        self.retransmissions = 0               # 관찰용: 그중 재전송 횟수

    def step(self):
        self.now += 1

        # 1) 도착한 ACK를 전부 처리
        while True:
            pkt = self.ack_ch.receive()
            if pkt is None:
                break
            kind, seq = pkt
            if kind == "ACK" and 0 <= seq < len(self.chunks):
                self.acked.add(seq)            # 중복 ACK여도 set이라 문제 없음

        # 2) 윈도우 왼쪽 끝을 앞으로 민다
        while self.base in self.acked:
            self.base += 1
        if self.base >= len(self.chunks):
            return False                       # 전부 ACK됨 -> 종료 (R6)

        # 3) 윈도우 안에서 (처음 보내는 것) 또는 (타임아웃된 것)을 전송
        for seq in range(self.base, min(self.base + self.WINDOW, len(self.chunks))):
            if seq in self.acked:
                continue
            first_time = seq not in self.sent_at
            if first_time or self.now - self.sent_at[seq] >= self.TIMEOUT:
                self.data_ch.send(("DATA", seq, self.chunks[seq]))
                self.sent_at[seq] = self.now
                self.transmissions += 1
                self.retransmissions += not first_time
        return True


class Receiver:
    """Selective Repeat 수신자.

    - 순서가 뒤바뀌어 온 조각은 버퍼에 보관했다가, 빈칸이 채워지면 순서대로 내보낸다 (R2)
    - 이미 받은 조각이 또 와도 ACK는 다시 보낸다: 지난번 ACK가 손실됐을 수 있기 때문
    - 하지만 데이터는 절대 두 번 쓰지 않는다 (R3)
    """

    def __init__(self, data_channel, ack_channel):
        self.data_ch = data_channel            # 데이터를 받는 채널 (up)
        self.ack_ch = ack_channel              # ACK를 보내는 채널 (down)
        self.buffer = {}                       # 먼저 도착한 조각: 번호 -> 바이트
        self.expected = 0                      # 다음에 출력해야 할 번호
        self.output = bytearray()

    def step(self):
        while True:
            pkt = self.data_ch.receive()
            if pkt is None:
                break
            kind, seq, payload = pkt
            if kind != "DATA":
                continue
            self.ack_ch.send(("ACK", seq))     # ACK는 항상 보낸다 (중복이어도)
            if seq >= self.expected and seq not in self.buffer:
                self.buffer[seq] = payload     # 처음 보는 조각만 저장
            while self.expected in self.buffer:   # 연속된 조각을 순서대로 출력
                self.output += self.buffer.pop(self.expected)
                self.expected += 1

    def data(self):
        return bytes(self.output)


# ------------------------------------------------------------------- harness
def verify(seed=246, size=2000, max_steps=200_000):
    original = bytes(random.Random(seed).getrandbits(8) for _ in range(size))
    up, down = UnreliableChannel(seed), UnreliableChannel(seed + 1)

    # Data goes out over `up`, ACKs come back over `down`. Both are unreliable.
    sender = Sender(up, down, original)
    receiver = Receiver(up, down)

    for _ in range(max_steps):
        alive = sender.step()
        receiver.step()
        if not alive and len(receiver.data() or b"") >= size:
            break

    got = receiver.data() or b""
    ok = hashlib.sha256(got).hexdigest() == hashlib.sha256(original).hexdigest()
    print(f"  bytes    sent {size}   received {len(got)}")
    print(f"  channel  {up.stats}")
    print(f"  result   {'IDENTICAL' if ok else 'CORRUPTED OR INCOMPLETE'}")
    return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true")
    p.add_argument("--seed", type=int, default=246)
    a = p.parse_args()
    raise SystemExit(verify(a.seed) if a.verify else p.print_help())