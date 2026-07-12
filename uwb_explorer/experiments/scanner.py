"""The active UWB scanner — the "nmap for UWB".

Sweeps the PHY space (channels x preamble codes) and, for each combo, actively
polls with ``initf`` and folds any reply into a discovered-devices model. As
with the repo's poll_once/board_loop split and :mod:`uwb_explorer.webmodel`,
all hardware, threads, and real time are kept OUT of the tested seam:

  * :func:`sweep_plan` is a pure cartesian product of the combos to visit.
  * :class:`ScanResults` folds parser Events into discoveries, with the clock
    INJECTED as an explicit ``timestamp`` argument so it never calls real time.
  * :class:`ScannerController` is a start/stop/status controller (duck-typed
    for :class:`uwb_explorer.experiments.control.Dispatcher`) that drives one
    combo per :meth:`~ScannerController.step` — no threads or sleeps.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from uwb_explorer.parser import Ack, ListenerFrame, RangingResult

# range-entry statuses that count as a successful reply to our poll
_OK_STATUSES = {"Ok", "SUCCESS"}

DEFAULT_CHANNELS = (5, 9)
DEFAULT_PCODES = (9, 10, 11, 12)


@dataclass
class SweepStep:
    """One PHY combo to probe: a channel and a preamble code."""

    channel: int
    pcode: int


def sweep_plan(
    channels: tuple[int, ...] = DEFAULT_CHANNELS,
    pcodes: tuple[int, ...] = DEFAULT_PCODES,
) -> list[SweepStep]:
    """Cartesian product of channels x pcodes, channel outer / pcode inner."""
    return [SweepStep(channel=ch, pcode=pc) for ch in channels for pc in pcodes]


class ScanResults:
    """Folds parser Events into discovered devices keyed by (addr, combo).

    Pure aggregation: the clock is injected via ``record``'s ``timestamp``, so
    the model never touches real time and the tests stay deterministic.
    """

    def __init__(self):
        # (addr, channel, pcode) -> discovery dict
        self._devices: dict[tuple[str, int, int], dict] = {}

    def _hit(self, addr: str, step: SweepStep, timestamp: float,
             rssi: float | None = None) -> None:
        key = (addr, step.channel, step.pcode)
        d = self._devices.get(key)
        if d is None:
            self._devices[key] = {
                "addr": addr,
                "channel": step.channel,
                "pcode": step.pcode,
                "reply_count": 1,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "rssi": rssi,
            }
            return
        d["reply_count"] += 1
        d["last_seen"] = timestamp
        if rssi is not None:
            d["rssi"] = rssi

    def record(self, step: SweepStep, event, timestamp: float) -> None:
        """Fold one parser event heard while probing ``step`` into the model."""
        if isinstance(event, RangingResult):
            for entry in event.results:
                if entry.status in _OK_STATUSES:
                    self._hit(entry.addr, step, timestamp)
        elif isinstance(event, (Ack, ListenerFrame)):
            # NOT a discovery. A bare "ok" Ack is the CLI acking our own `initf`
            # command echo (dl1) — counting it fabricated a phantom device on
            # every scan; a real responder arrives as a RangingResult. A
            # promiscuous ListenerFrame isn't a reply to our active poll either.
            pass

    def to_list(self) -> list[dict]:
        """JSON-able list of discoveries, newest additions last."""
        return [dict(d) for d in self._devices.values()]


class ScannerController:
    """Start/stop/status controller that sweeps the PHY space one combo at a time.

    Takes an already-detected :class:`~uwb_explorer.device.Device` and an
    injected ``now`` clock; sweeping is driven a combo at a time (the first on
    :meth:`start`, each subsequent on :meth:`step`) so no threads or sleeps are
    needed.
    """

    def __init__(self, device, now=time.monotonic, sleep=time.sleep, dwell=1.0):
        self._device = device
        self._now = now
        # after firing `initf` a combo must DWELL before draining: on hardware a
        # responder's ranging blocks arrive ~200ms later (every ranging period),
        # so an immediate poll sees nothing. `sleep`/`dwell` are injected so the
        # tests stay instant (dwell=0 or a no-op sleep) while hardware waits ~1s.
        self._sleep = sleep
        self._dwell = dwell
        self._plan: list[SweepStep] = []
        self._results = ScanResults()
        self._index = 0
        self._running = False
        self._channels: tuple[int, ...] = DEFAULT_CHANNELS
        self._pcodes: tuple[int, ...] = DEFAULT_PCODES

    @staticmethod
    def _parse_csv(value: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
        # List values may arrive comma-separated (from a direct caller) or with
        # ";" as the sub-delimiter (the wire form — see docs/EXPERIMENTS.md —
        # because control.py reserves "," for the key=value pair separator).
        if not value or not value.strip():
            return default
        return tuple(
            int(tok) for tok in re.split(r"[,;]", value) if tok.strip()
        )

    def _run_step(self, step: SweepStep) -> None:
        ch, pc = step.channel, step.pcode
        self._device.set_uwbcfg(CHAN=ch, TXCODE=pc, RXCODE=pc)
        # drop the board's config-set "ok" so it can't be miscounted as a
        # reply to the poll we are about to send
        self._device.session.flush_input()
        self._device.start_initf(CHAN=ch, PCODE=pc)
        # dwell so a responder on this combo has time to answer before we drain
        # (its ranging blocks arrive one ranging-period later, not instantly)
        self._sleep(self._dwell)
        for ev in self._device.poll_events():
            self._results.record(step, ev, self._now())

    def start(self, args: dict) -> None:
        self._channels = self._parse_csv(args.get("channels"), DEFAULT_CHANNELS)
        self._pcodes = self._parse_csv(args.get("pcodes"), DEFAULT_PCODES)
        self._plan = sweep_plan(self._channels, self._pcodes)
        self._results = ScanResults()
        self._index = 0
        self._running = True
        if self._plan:
            self._run_step(self._plan[0])
            self._index = 1

    def step(self) -> bool:
        """Drive the next combo; return False (driving nothing) when exhausted."""
        if self._index >= len(self._plan):
            return False
        self._run_step(self._plan[self._index])
        self._index += 1
        return True

    def stop(self, args: dict) -> None:
        self._device.stop()
        self._running = False

    def status(self, args: dict) -> dict:
        return {
            "running": self._running,
            "total": len(self._plan),
            "step": self._index,
            "channels": list(self._channels),
            "pcodes": list(self._pcodes),
            "devices": self._results.to_list(),
        }
