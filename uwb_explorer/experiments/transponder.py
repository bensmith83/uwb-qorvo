"""The permissive UWB transponder — a "discoverable UWB landmark".

The mirror image of :mod:`uwb_explorer.experiments.scanner`: instead of
actively polling with ``initf`` and folding replies, it cycles the same PHY
space (channels x preamble codes) driving ``respf`` so the board ANSWERS polls
from unknown initiators, and aggregates the answered polls (initiator addr,
count, distance/rssi) into a report. As with the scanner, all hardware,
threads, and real time are kept OUT of the tested seam:

  * :func:`config_plan` reuses the scanner's :class:`SweepStep` /
    :func:`sweep_plan` — a PHY combo is a PHY combo whether we poll or answer.
  * :class:`TransponderResults` folds parser Events into an answered-polls
    report keyed by (initiator addr, combo), with the clock INJECTED as an
    explicit ``timestamp`` argument so it never calls real time.
  * :class:`TransponderController` is a start/stop/status controller (duck-typed
    for :class:`uwb_explorer.experiments.control.Dispatcher`) that drives one
    combo per :meth:`~TransponderController.step` — no threads or sleeps.
"""

from __future__ import annotations

import re
import time

from uwb_explorer.parser import Ack, ListenerFrame, RangingResult

from uwb_explorer.experiments.scanner import (
    DEFAULT_CHANNELS,
    DEFAULT_PCODES,
    SweepStep,
    sweep_plan,
)

# range-entry statuses that count as a poll we successfully answered
_OK_STATUSES = {"Ok", "SUCCESS"}

# sentinel addr for an anonymous "ok" answer (no source address)
_ACK_ADDR = "ok"


def config_plan(
    channels: tuple[int, ...] = DEFAULT_CHANNELS,
    pcodes: tuple[int, ...] = DEFAULT_PCODES,
) -> list[SweepStep]:
    """Cartesian product of channels x pcodes, channel outer / pcode inner."""
    return sweep_plan(channels, pcodes)


class TransponderResults:
    """Folds parser Events into answered polls keyed by (addr, combo).

    Pure aggregation: the clock is injected via ``record``'s ``timestamp``, so
    the model never touches real time and the tests stay deterministic.
    """

    def __init__(self):
        # (addr, channel, pcode) -> answered-poll dict
        self._polls: dict[tuple[str, int, int], dict] = {}

    def _hit(self, addr: str, step: SweepStep, timestamp: float,
             distance_cm: int | None = None, rssi: float | None = None) -> None:
        key = (addr, step.channel, step.pcode)
        d = self._polls.get(key)
        if d is None:
            self._polls[key] = {
                "addr": addr,
                "channel": step.channel,
                "pcode": step.pcode,
                "poll_count": 1,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "distance_cm": distance_cm,
                "rssi": rssi,
            }
            return
        d["poll_count"] += 1
        d["last_seen"] = timestamp
        if distance_cm is not None:
            d["distance_cm"] = distance_cm
        if rssi is not None:
            d["rssi"] = rssi

    def record(self, step: SweepStep, event, timestamp: float) -> None:
        """Fold one parser event heard while answering on ``step``."""
        if isinstance(event, RangingResult):
            for entry in event.results:
                if entry.status in _OK_STATUSES:
                    self._hit(
                        entry.addr, step, timestamp,
                        distance_cm=getattr(entry, "distance_cm", None),
                        rssi=getattr(entry, "rssi", None),
                    )
        elif isinstance(event, Ack):
            if event.ok:
                self._hit(_ACK_ADDR, step, timestamp)
        elif isinstance(event, ListenerFrame):
            # a promiscuous sniff frame is not an answered poll
            pass

    def to_list(self) -> list[dict]:
        """JSON-able list of answered polls, newest additions last."""
        return [dict(d) for d in self._polls.values()]


class TransponderController:
    """Start/stop/status controller that answers polls across the PHY space.

    Takes an already-detected :class:`~uwb_explorer.device.Device` and an
    injected ``now`` clock; cycling is driven a combo at a time (the first on
    :meth:`start`, each subsequent on :meth:`step`) so no threads or sleeps are
    needed.
    """

    def __init__(self, device, now=time.monotonic):
        self._device = device
        self._now = now
        self._plan: list[SweepStep] = []
        self._results = TransponderResults()
        self._index = 0
        self._running = False

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
        # drop the board's config-set "ok" so it can't be miscounted as an
        # answered poll on the respf we are about to start
        self._device.session.flush_input()
        self._device.start_respf(CHAN=ch, PCODE=pc)
        for ev in self._device.poll_events():
            self._results.record(step, ev, self._now())

    def start(self, args: dict) -> None:
        channels = self._parse_csv(args.get("channels"), DEFAULT_CHANNELS)
        pcodes = self._parse_csv(args.get("pcodes"), DEFAULT_PCODES)
        self._plan = config_plan(channels, pcodes)
        self._results = TransponderResults()
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
            "answered": self._results.to_list(),
        }
