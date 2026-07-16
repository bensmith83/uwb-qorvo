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

The start/step/stop stepping skeleton, the repeated-poll dwell, and the
running-clears-on-exhaustion behavior all live in the shared
:class:`~uwb_explorer.experiments.sweep.SweepController` base (bead
uwb-qorvo-1hu.20) — this module supplies only the scanner-specific hooks: the
``initf`` ranging role, :class:`ScanResults`, and the ``"devices"`` status key.
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
    "ScanResults",
    "ScannerController",
    "SweepStep",
    "sweep_plan",
]

# range-entry statuses that count as a successful reply to our poll
_OK_STATUSES = {"Ok", "SUCCESS"}


class ScanResults:
    """Folds parser Events into discovered devices keyed by (addr, combo).

    Pure aggregation: the clock is injected via ``record``'s ``timestamp``, so
    the model never touches real time and the tests stay deterministic.
    """

    def __init__(self):
        # (addr, channel, pcode) -> discovery dict
        self._devices: dict[tuple[str, int, int], dict] = {}
        # monotonic count of OK replies folded in; lets the shared base's
        # repeated-poll dwell loop detect that a fresh drain produced a hit
        # (so it can stop early — see SweepController._run_step).
        self._hits = 0

    @property
    def hits(self) -> int:
        """Total OK replies recorded so far (new device OR repeat reply)."""
        return self._hits

    def _hit(self, addr: str, step: SweepStep, timestamp: float,
             rssi: float | None = None) -> None:
        self._hits += 1
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


class ScannerController(SweepController):
    """Sweeps the PHY space one combo at a time, actively polling with ``initf``.

    See :class:`~uwb_explorer.experiments.sweep.SweepController` for the shared
    start/step/stop/status skeleton (including the repeated-poll dwell and
    running-clears-on-exhaustion behavior); this subclass supplies only the
    scanner-specific hooks.
    """

    _results_cls = ScanResults
    _results_key = "devices"

    def _start_ranging(self, ch: int, pc: int) -> None:
        self._device.start_initf(CHAN=ch, PCODE=pc)
