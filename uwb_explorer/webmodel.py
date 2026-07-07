"""Pure state model for the phone web dashboard (the UWB 'Geiger counter').

Fed raw LSTAT counter dicts from the board (monotonic PHY event counters),
this tracks per-poll deltas, a rolling activity level, sparkline history, and
peaks — everything the phone page needs, as plain JSON-able values. It holds
no serial or HTTP state, so it is unit-tested without hardware.
"""

from __future__ import annotations

from collections import deque

# Any increase in one of these means a UWB frame's energy reached the antenna,
# even if the frame itself is undecodable (encrypted STS, foreign PHY, ...).
#   SFDD = start-of-frame delimiter detections (strongest "we heard it")
#   PHE  = PHY header errors (frame started, header undecodable)
#   CRCB = bad CRC (frame received, integrity failed)
#   CRCG = good CRC (frame fully decoded)
HIT_COUNTERS = ("SFDD", "PHE", "CRCB", "CRCG")


class DetectorState:
    def __init__(self, history: int = 120):
        self._prev: dict | None = None
        self._history: deque[int] = deque(maxlen=history)
        self.hits = 0
        self.total = 0
        self.peak = 0
        self.decoded = 0
        self.level = "idle"
        self.delta: dict[str, int] = dict.fromkeys(HIT_COUNTERS, 0)
        self.channel: int | None = None
        self.pcode: int | None = None
        # "waiting" (no board yet), "live" (polling), or "error"
        self.status = "waiting"

    def set_config(self, channel: int | None = None, pcode: int | None = None) -> None:
        if channel is not None:
            self.channel = channel
        if pcode is not None:
            self.pcode = pcode

    def set_status(self, status: str) -> None:
        self.status = status

    @staticmethod
    def _level(hits: int) -> str:
        if hits <= 0:
            return "idle"
        if hits < 10:
            return "low"
        if hits < 100:
            return "medium"
        return "high"

    def update(self, lstat: dict) -> dict:
        cur = {k: int(lstat.get(k, 0)) for k in HIT_COUNTERS}
        if self._prev is None:
            # First reading only establishes a baseline: the counters carry
            # history from before we started listening, so the delta is zero.
            self.delta = dict.fromkeys(HIT_COUNTERS, 0)
            self.hits = 0
            self.decoded = 0
        else:
            # A decrease means the listener was restarted and counters reset;
            # clamp negatives to zero rather than report phantom activity.
            delta = {k: max(0, cur[k] - self._prev[k]) for k in HIT_COUNTERS}
            self.delta = delta
            self.hits = sum(delta.values())
            self.decoded = delta["CRCG"]
        self._prev = cur
        self.level = self._level(self.hits)
        self.total += self.hits
        self.peak = max(self.peak, self.hits)
        self._history.append(self.hits)
        return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "hits": self.hits,
            "level": self.level,
            "total": self.total,
            "peak": self.peak,
            "decoded": self.decoded,
            "delta": dict(self.delta),
            "history": list(self._history),
            "channel": self.channel,
            "pcode": self.pcode,
            "status": self.status,
        }
