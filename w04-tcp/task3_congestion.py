#!/usr/bin/env python3
"""Week 4 · Task 3 — Beat the fixed window.

Textbook §3.7.

`FixedWindow` is a sender that never adapts. It picks a window and keeps it,
forever, no matter what the network says back. It is not a strawman: it is what
you get if you skip congestion control entirely, and it was the internet's
actual failure mode in October 1986.

Write `YourControl` and beat it on the harness:

    python3 bench.py
    python3 bench.py --yours

The interface is two events and one number:

    .window        how many packets you are willing to have in flight
    .on_ack()      one packet made it there and back
    .on_loss()     a packet was dropped, or timed out waiting for its ACK

That is all the information a real TCP sender has. It cannot see the queue,
it cannot see the link rate, and neither can you. You infer them from these
two events, which is the entire idea of §3.7.
"""


class FixedWindow:
    """Send 64 packets at a time and never listen."""

    def __init__(self):
        self.window = 64

    def on_ack(self):
        pass

    def on_loss(self):
        pass


class YourControl:
    """Slow start + 완만한 AIMD + 손실 사건당 한 번만 반응.

    1) Slow start: 첫 손실 전에는 ACK마다 window += 1
       -> RTT마다 윈도우가 두 배가 되어 빠르게 파이프(약 20)를 채운다
    2) Congestion avoidance: ssthresh 이후에는 ACK마다 window += 1/window
       -> RTT마다 1씩만 늘려 큐를 천천히 채운다
    3) 손실 시 window를 0.5가 아니라 0.7배로 줄인다
       -> 드롭 지점(파이프 20 + 큐 10 = 30) 근처에서 줄여도 약 21이 되어
          파이프를 거의 꽉 채운 상태를 유지한다
    4) 큐가 한 번 넘치면 여러 패킷이 한꺼번에 타임아웃되어 on_loss가 연달아 온다.
       그것은 혼잡 사건 하나이므로, 줄인 뒤 한 윈도우만큼 ACK가 올 때까지는
       추가 손실을 무시한다. (안 그러면 0.7^n 으로 윈도우가 무너진다)
    """

    BACKOFF = 0.7

    def __init__(self):
        self.window = 1.0
        self.ssthresh = float("inf")   # 첫 손실 전에는 무한대 -> slow start
        self.acks = 0                  # 지금까지 받은 ACK 수
        self.ignore_until = 0          # 이 ACK 수에 도달할 때까지 손실 무시

    def on_ack(self):
        self.acks += 1
        if self.window < self.ssthresh:
            self.window += 1                    # slow start
        else:
            self.window += 1 / self.window      # congestion avoidance

    def on_loss(self):
        if self.acks < self.ignore_until:
            return                              # 같은 혼잡 사건의 나머지 손실
        self.ssthresh = max(2.0, self.window * self.BACKOFF)
        self.window = self.ssthresh
        self.ignore_until = self.acks + int(self.window)