"""The permissive UWB transponder — a "discoverable UWB landmark".

The mirror image of :mod:`uwb_explorer.experiments.scanner`: instead of
actively polling with ``initf`` and folding replies, it cycles the same PHY
space (channels x preamble codes) driving ``respf`` so the board ANSWERS polls
from unknown initiators, and aggregates the answered polls (initiator addr,
count, distance/rssi) into a report. As with the scanner, all hardware,
threads, and real time are kept OUT of the tested seam:

  * :func:`config_plan` reuses the shared :class:`SweepStep` /
    :func:`sweep_plan` — a PHY combo is a PHY combo whether we poll or answer.
  * :class:`TransponderResults` folds parser Events into an answered-polls
    report keyed by (initiator addr, combo), with the clock INJECTED as an
    explicit ``timestamp`` argument so it never calls real time.
  * :class:`TransponderController` is a start/stop/status controller (duck-typed
    for :class:`uwb_explorer.experiments.control.Dispatcher`) that drives one
    combo per :meth:`~TransponderController.step` — no threads or sleeps.

The start/step/stop stepping skeleton, the repeated-poll dwell (bug
uwb-qorvo-4hg), and the running-clears-on-exhaustion behavior (bug
uwb-qorvo-09r) all live in the shared
:class:`~uwb_explorer.experiments.sweep.SweepController` base (bead
uwb-qorvo-1hu.20) — hardware-validated fixes made for the scanner that the
transponder now inherits too. This module supplies only the
transponder-specific hooks: the ``respf`` ranging role, :class:`TransponderResults`,
and the ``"answered"`` status key.
"""

from __future__ import annotations

from uwb_explorer.parser import Ack, ListenerFrame, RangingResult

from uwb_explorer.experiments.sweep import (
    DEFAULT_CHANNELS,
    DEFAULT_PCODES,
    SweepController,
    SweepStep,
    sweep_plan,
)

__all__ = [
    "DEFAULT_CHANNELS",
    "DEFAULT_PCODES",
    "SweepStep",
    "TransponderController",
    "TransponderResults",
    "config_plan",
    "sweep_plan",
]

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
        # monotonic count of answered polls folded in; lets the shared base's
        # repeated-poll dwell loop detect that a fresh drain produced a hit
        # (so it can stop early — see SweepController._run_step).
        self._hits = 0

    @property
    def hits(self) -> int:
        """Total answered polls recorded so far (new record OR repeat poll)."""
        return self._hits

    def _hit(self, addr: str, step: SweepStep, timestamp: float,
             distance_cm: int | None = None, rssi: float | None = None) -> None:
        self._hits += 1
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


class TransponderController(SweepController):
    """Cycles the PHY space one combo at a time, answering polls with ``respf``.

    See :class:`~uwb_explorer.experiments.sweep.SweepController` for the shared
    start/step/stop/status skeleton (including the repeated-poll dwell and
    running-clears-on-exhaustion behavior); this subclass supplies only the
    transponder-specific hooks.
    """

    _results_cls = TransponderResults
    _results_key = "answered"

    def _start_ranging(self, ch: int, pc: int) -> None:
        self._device.start_respf(CHAN=ch, PCODE=pc)
