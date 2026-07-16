"""Shared Template-Method base for the PHY-sweep experiment controllers.

:mod:`uwb_explorer.experiments.scanner` (active poll / "nmap for UWB") and
:mod:`uwb_explorer.experiments.transponder` (permissive answer / "discoverable
landmark") both cycle the same channel x preamble-code PHY space one combo at
a time, and differ only in which ranging role they play. This module hosts
what they share (bead uwb-qorvo-1hu.20) so a hardware-found fix lands ONCE and
benefits both:

  * :func:`sweep_plan` — the cartesian product of the combos to visit.
  * :class:`SweepController` — the start/step/stop/status controller skeleton
    (duck-typed for :class:`uwb_explorer.experiments.control.Dispatcher`),
    including the corrected, hardware-validated lifecycle:

    1. **Repeated-poll dwell** (bug uwb-qorvo-4hg): after firing the
       role-specific ranging command, the dwell budget is spent as several
       short sub-poll slices (``poll_slices``, default 5), draining
       ``poll_events()`` after each slice and stopping early once a fresh hit
       is recorded. On hardware a responder's first ranging block can arrive
       a few hundred ms after the ranging command — well after a single
       sleep-then-drain would have already given up — so a lone drain on the
       very first combo could see nothing while every later, re-polled combo
       discovered fine.
    2. **running clears on exhaustion** (bug uwb-qorvo-09r): :meth:`step`
       clears ``_running`` once the plan is exhausted, and :meth:`status`
       reports ``running`` as ``False`` from that point on with no explicit
       ``stop()`` — so the port-arbiter pump handoff (see
       :mod:`uwb_explorer.experiments.arbiter`) resumes the passive listener
       on natural completion, not just on an explicit stop.

As with the repo's poll_once/board_loop split, all hardware, threads, and real
time are kept OUT of the tested seam: the clock (``now``) and the dwell sleep
(``sleep``) are both injected, so tests stay instant and deterministic.

Subclasses (:class:`~uwb_explorer.experiments.scanner.ScannerController`,
:class:`~uwb_explorer.experiments.transponder.TransponderController`) supply
three hooks — the ranging role differs, and so does the results object type,
its "hit" semantics, and the status key it is reported under:

  * ``_results_cls`` (class attribute) — the Results type to instantiate.
    Must satisfy the base's duck-typed contract: ``record(step, event,
    timestamp)``, ``to_list()``, and a monotonic ``hits`` count property (used
    by the repeated-poll loop to detect a fresh hit and stop dwelling early).
  * ``_results_key`` (class attribute) — the :meth:`status` dict key the
    results list is reported under (``"devices"`` for the scanner,
    ``"answered"`` for the transponder).
  * ``_start_ranging(ch, pc)`` (method) — fires the role-specific ranging
    command for one PHY combo (``start_initf`` for the scanner, ``start_respf``
    for the transponder).
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_CHANNELS = (5, 9)
DEFAULT_PCODES = (9, 10, 11, 12)


@dataclass
class SweepStep:
    """One PHY combo to probe or answer on: a channel and a preamble code."""

    channel: int
    pcode: int


def sweep_plan(
    channels: tuple[int, ...] = DEFAULT_CHANNELS,
    pcodes: tuple[int, ...] = DEFAULT_PCODES,
) -> list[SweepStep]:
    """Cartesian product of channels x pcodes, channel outer / pcode inner."""
    return [SweepStep(channel=ch, pcode=pc) for ch in channels for pc in pcodes]


class SweepController(ABC):
    """Start/stop/status controller that cycles the PHY space one combo at a time.

    Takes an already-detected :class:`~uwb_explorer.device.Device` and an
    injected ``now`` clock; the cycle is driven a combo at a time (the first on
    :meth:`start`, each subsequent on :meth:`step`) so no threads or sleeps are
    needed to drive it — only the dwell inside a single combo uses the injected
    ``sleep``.

    Concrete subclasses must set the ``_results_cls`` / ``_results_key`` class
    attributes and implement :meth:`_start_ranging`; see the module docstring.
    """

    _results_cls: type | None = None
    _results_key: str | None = None

    def __init__(self, device, now=time.monotonic, sleep=time.sleep, dwell=1.0,
                 poll_slices=5):
        if self._results_cls is None or self._results_key is None:
            raise NotImplementedError(
                f"{type(self).__name__} must set _results_cls and _results_key"
            )
        self._device = device
        self._now = now
        # after firing the ranging command a combo must DWELL before draining:
        # on hardware a reply's ranging blocks arrive ~200ms later (every
        # ranging period), so an immediate poll sees nothing. `sleep`/`dwell`
        # are injected so the tests stay instant (dwell=0 or a no-op sleep)
        # while hardware waits ~1s.
        self._sleep = sleep
        self._dwell = dwell
        # the dwell is spent as several short sub-poll slices, draining after
        # each, so a reply that lands PART-WAY through the window is caught
        # (bug 4hg: the first combo's ranging block arrives a few hundred ms
        # after the ranging command, and a single sleep-then-drain missed it
        # while every re-polled later combo discovered fine). >=1 slice; each
        # is dwell/poll_slices.
        self._poll_slices = max(1, int(poll_slices))
        self._plan: list[SweepStep] = []
        self._results = self._results_cls()
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

    @abstractmethod
    def _start_ranging(self, ch: int, pc: int) -> None:
        """Fire the role-specific ranging command for one PHY combo."""

    def _run_step(self, step: SweepStep) -> None:
        ch, pc = step.channel, step.pcode
        self._device.set_uwbcfg(CHAN=ch, TXCODE=pc, RXCODE=pc)
        # drop the board's config-set "ok" so it can't be miscounted as a
        # reply to the ranging command we are about to send
        self._device.session.flush_input()
        self._start_ranging(ch, pc)
        # Spend the dwell budget as several short sub-poll slices, draining
        # after each: on hardware a reply's ranging blocks arrive ~200ms after
        # the ranging command (and every ranging period after), so a single
        # sleep-then-drain can miss the reply — especially on the very FIRST
        # combo (bug 4hg). Poll repeatedly until a hit is recorded OR the
        # budget is exhausted.
        hits_before = self._results.hits
        slice_dwell = self._dwell / self._poll_slices
        for _ in range(self._poll_slices):
            self._sleep(slice_dwell)
            for ev in self._device.poll_events():
                self._results.record(step, ev, self._now())
            if self._results.hits > hits_before:
                break  # got a hit on this combo — no need to burn the rest

    def start(self, args: dict) -> None:
        self._channels = self._parse_csv(args.get("channels"), DEFAULT_CHANNELS)
        self._pcodes = self._parse_csv(args.get("pcodes"), DEFAULT_PCODES)
        self._plan = sweep_plan(self._channels, self._pcodes)
        self._results = self._results_cls()
        self._index = 0
        self._running = True
        if self._plan:
            self._run_step(self._plan[0])
            self._index = 1

    def step(self) -> bool:
        """Drive the next combo; return False (driving nothing) when exhausted.

        On exhaustion the sweep is DONE, so clear ``_running`` (bug 09r): the
        pump handoff keys off this to release the port arbiter and resume the
        passive listener, with no explicit stop needed.
        """
        if self._index >= len(self._plan):
            self._running = False
            return False
        self._run_step(self._plan[self._index])
        self._index += 1
        return True

    def stop(self, args: dict) -> None:
        self._device.stop()
        self._running = False

    def status(self, args: dict) -> dict:
        # "running" reflects DONE once every combo has been driven (bug 09r):
        # the flag alone stayed True forever after a natural exhaustion (only
        # stop() cleared it), so report running only while combos remain.
        running = self._running and self._index < len(self._plan)
        return {
            "running": running,
            "total": len(self._plan),
            "step": self._index,
            "channels": list(self._channels),
            "pcodes": list(self._pcodes),
            self._results_key: self._results.to_list(),
        }
