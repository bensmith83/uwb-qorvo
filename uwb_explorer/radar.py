"""Rolling model of live UWB activity, feeding the dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .mac import decode_frame
from .parser import Event, ListenerFrame, RangingResult


@dataclass
class Contact:
    addr: str
    passive: bool = False           # seen only via sniffed frames, not ranging
    last_distance_cm: int | None = None
    samples: int = 0                # successful ranging measurements
    misses: int = 0                 # failed ranging measurements
    frames: int = 0                 # sniffed frames attributed to this addr
    last_rssi_dbm: float | None = None
    distance_history: deque = field(default_factory=deque)


class RadarModel:
    def __init__(self, history: int = 120):
        self._history = history
        self.contacts: dict[str, Contact] = {}
        self.frame_count = 0
        self.range_count = 0
        self.last_rssi_dbm: float | None = None

    def _contact(self, addr: str) -> Contact:
        c = self.contacts.get(addr)
        if c is None:
            c = Contact(addr=addr, distance_history=deque(maxlen=self._history))
            self.contacts[addr] = c
        return c

    def ingest(self, ev: Event | None) -> None:
        if ev is None:
            return
        if isinstance(ev, RangingResult):
            self._ingest_ranging(ev)
        elif isinstance(ev, ListenerFrame):
            self._ingest_frame(ev)

    def _ingest_ranging(self, ev: RangingResult) -> None:
        for r in ev.results:
            c = self._contact(r.addr)
            c.passive = False
            if r.distance_cm is not None and r.status.lower().startswith("ok"):
                c.last_distance_cm = r.distance_cm
                c.samples += 1
                c.distance_history.append(r.distance_cm)
                self.range_count += 1
            else:
                c.misses += 1

    def _ingest_frame(self, ev: ListenerFrame) -> None:
        self.frame_count += 1
        if ev.rssi_dbm is not None:
            self.last_rssi_dbm = ev.rssi_dbm
        info = decode_frame(ev.payload)
        if info.src:
            c = self._contact(info.src)
            if c.samples == 0:
                c.passive = True
            c.frames += 1
            c.last_rssi_dbm = ev.rssi_dbm

    def stats(self) -> dict:
        return {
            "contacts": len(self.contacts),
            "frames": self.frame_count,
            "ranges": self.range_count,
        }
