"""The malformed-frame fuzzer — a DELIBERATELY-triggered UWB fuzz-case fire
(bead uwb-qorvo-1hu.16).

*** AUTHORIZED SECURITY-RESEARCH TOOLING. Fire fuzz cases ONLY against
*** devices you own or are explicitly authorized to test. This experiment
*** exists to probe how a nearby UWB device reacts to malformed 802.15.4z
*** frames of your own transmission — never point it at infrastructure or
*** third-party hardware. There is no auto-fire path anywhere in this module
*** or the web panel that drives it: every fire is one deliberate button
*** press, one opcode, one frame.

Unlike :mod:`uwb_explorer.experiments.scanner`/``transponder`` (which sweep
the PHY space) or :mod:`uwb_explorer.experiments.beacon` (which transmits
periodically via firmware-owned TCFM), the fuzzer fires ONE malformed frame
per :meth:`~FuzzerController.start` call and then listens for whatever
happens next:

  1. emit ``fuzztx <case_id>`` over the CLI serial link — the wire command the
     .15 firmware (deferred to hardware, not implemented here) understands to
     transmit one malformed test frame matching the fixed case catalog below.
  2. switch the board to LISTENER mode (:meth:`Device.start_listener`) so any
     reaction from a nearby device — an error frame, a retry, a probe, silence
     — is captured promiscuously.
  3. drain :meth:`Device.poll_events` right after the switch and fold whatever
     showed up into a structured, timestamped "reactions" log.

As with the repo's other controllers, real time is kept OUT of the tested
seam: the clock (``now``) is injected so reaction timestamps are
deterministic. There is no ``step()`` and no injected sleep/dwell — a single
fire is a single synchronous action, not a multi-combo sweep (see
:mod:`uwb_explorer.experiments.beacon` for the same no-step shape). Real TX
needs the .15 firmware; here we unit-test that the right ``fuzztx <id>`` is
sent and that scripted post-fire LISTENER events land in the reactions log.

:class:`FuzzerController` is a plain start/stop/status controller, duck-typed
for :class:`uwb_explorer.experiments.control.Dispatcher` on letter ``Z``.
"""

from __future__ import annotations

import time

from uwb_explorer.parser import Ack, InfoBlock, ListenerFrame, RangingResult

# Fuzz-case catalog, ordered by id (FIXED CONTRACT — the .15 firmware agent
# uses the exact same ids; do not renumber or reorder without updating both
# sides and docs/EXPERIMENTS.md).
CASES: list[dict[str, object]] = [
    {"id": 0, "name": "bad-crc"},
    {"id": 1, "name": "invalid-frametype"},
    {"id": 2, "name": "oversized-phr"},
    {"id": 3, "name": "truncated-mac"},
    {"id": 4, "name": "illegal-sts"},
]
CASE_IDS: dict[str, int] = {c["name"]: c["id"] for c in CASES}
CASE_NAMES: dict[int, str] = {c["id"]: c["name"] for c in CASES}
DEFAULT_CASE = "bad-crc"

__all__ = [
    "CASES",
    "CASE_IDS",
    "CASE_NAMES",
    "DEFAULT_CASE",
    "FuzzerController",
]


def _describe_reaction(event, timestamp: float) -> dict:
    """Render one post-fire event into a JSON-able, timestamped reaction."""
    if isinstance(event, ListenerFrame):
        return {
            "timestamp": timestamp,
            "type": "frame",
            "payload": event.payload.hex(),
            "offset": event.offset,
            "rssi_dbm": event.rssi_dbm,
            "first_path_dbm": event.first_path_dbm,
        }
    if isinstance(event, RangingResult):
        return {
            "timestamp": timestamp,
            "type": "ranging",
            "block": event.block,
            "results": [
                {"addr": r.addr, "status": r.status, "distance_cm": r.distance_cm}
                for r in event.results
            ],
        }
    if isinstance(event, Ack):
        return {"timestamp": timestamp, "type": "ack", "ok": event.ok}
    if isinstance(event, InfoBlock):
        return {"timestamp": timestamp, "type": "info", "data": event.data}
    return {"timestamp": timestamp, "type": "unknown"}


class FuzzerController:
    """Start/stop/status controller for one deliberate malformed-frame fire.

    Takes an already-detected :class:`~uwb_explorer.device.Device` and an
    injected ``now`` clock. ``start(args)`` fires ONE case and captures
    whatever LISTENER traffic follows; there is no threaded/periodic firing —
    each call to ``start`` is one deliberate shot. Duck-typed for
    :class:`uwb_explorer.experiments.control.Dispatcher`:
    ``start(args)``/``stop(args)``/``status(args)``.
    """

    def __init__(self, device, now=time.monotonic):
        self._device = device
        self._now = now
        self._running = False
        self._last_case: str | None = None
        self._reactions: list[dict] = []

    def start(self, args: dict) -> None:
        args = args or {}
        case_name = str(args.get("case") or "").strip() or DEFAULT_CASE
        if case_name not in CASE_IDS:
            raise ValueError(f"unknown fuzz case: {case_name!r}")
        case_id = CASE_IDS[case_name]

        self._last_case = case_name
        self._device.session.send(f"fuzztx {case_id}")
        # drop the board's ack for our own fuzztx command so it can't be
        # misread as a device REACTION once we switch to LISTENER and drain
        # (mirrors BeaconController.start's flush-before-fire convention —
        # see its module docstring for the same phantom-event caveat).
        self._device.session.flush_input()

        self._device.start_listener()
        self._reactions = [
            _describe_reaction(ev, self._now())
            for ev in self._device.poll_events()
        ]
        self._running = True

    def stop(self, args: dict) -> None:
        self._device.stop()
        self._running = False

    def status(self, args: dict) -> dict:
        return {
            "running": self._running,
            "last_case": self._last_case,
            "reactions": list(self._reactions),
        }
