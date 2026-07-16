"""The stock-firmware Beacon experiment controller — a periodic fixed-frame TX
beacon (bead uwb-qorvo-1hu.11).

Unlike :mod:`uwb_explorer.experiments.sweep`'s ``ScannerController`` /
``TransponderController``, the beacon does NOT sweep the PHY space and plays no
ranging role: it holds ONE fixed channel/preamble-code combo and drives stock
firmware's TCFM ("Test Continuous Frame Mode", :meth:`Device.tcfm`) to transmit
fixed test frames — no new firmware, no ``initf``/``respf``. TCFM's own
``count``/``interval`` arguments give the FIRMWARE ownership of the actual
periodicity, so — unlike the sweep controllers' injected dwell/sleep that steps
combo-to-combo on the Pi side — :class:`BeaconController` needs no injected
clock/sleep and no ``step()``: :meth:`~BeaconController.start` configures the
board once and fires a single ``tcfm`` call; the beacon then runs on its own
until :meth:`~BeaconController.stop`.

As with the other controllers, :class:`BeaconController` is a plain
start/stop/status controller, duck-typed for
:class:`uwb_explorer.experiments.control.Dispatcher` (so it slots into the same
half-duplex port-arbiter handoff as the scanner/transponder — see
:mod:`uwb_explorer.experiments.arbiter` — without re-implementing any of it;
having no ``step()`` simply means the arbiter's pump is a no-op for this letter,
which is correct since the beacon needs no Pi-side stepping).

NOTE on TCFM ``count``/``interval`` semantics: the exact units/behavior were not
confirmed against a live board's ``HELP TCFM`` output (mirrors the same caveat
already carried by :meth:`Device.set_antenna_delay`) — ``count=0`` is treated
here as "run until stopped" (the common Decawave/Qorvo CLI convention: a `0`
frame count with an interval loops indefinitely rather than sending zero
frames), and ``interval`` as a firmware-defined period between frames. Verify
against hardware before relying on exact timing.
"""

from __future__ import annotations

DEFAULT_CHANNEL = 5
DEFAULT_PCODE = 9
DEFAULT_INTERVAL = 1.0
DEFAULT_COUNT = 0  # 0 == run until stopped — see module docstring caveat


class BeaconController:
    """Start/stop/status controller for a periodic fixed-frame TX beacon.

    Takes an already-detected :class:`~uwb_explorer.device.Device`. NOT a sweep
    (see module docstring): one fixed PHY combo, configured once on
    :meth:`start`, transmitted via the firmware's own TCFM periodicity — no
    threads, no injected clock/sleep, no ``step()``. Duck-typed for
    :class:`uwb_explorer.experiments.control.Dispatcher`:
    ``start(args)``/``stop(args)``/``status(args)``.
    """

    def __init__(self, device):
        self._device = device
        self._running = False
        self._channel = DEFAULT_CHANNEL
        self._pcode = DEFAULT_PCODE
        self._interval = DEFAULT_INTERVAL
        self._count = DEFAULT_COUNT

    @staticmethod
    def _parse_int(value: str | None, default: int) -> int:
        if value is None or not str(value).strip():
            return default
        return int(value)

    @staticmethod
    def _parse_float(value: str | None, default: float) -> float:
        if value is None or not str(value).strip():
            return default
        return float(value)

    def start(self, args: dict) -> None:
        args = args or {}
        self._channel = self._parse_int(args.get("channel"), DEFAULT_CHANNEL)
        self._pcode = self._parse_int(args.get("pcode"), DEFAULT_PCODE)
        self._interval = self._parse_float(args.get("interval"), DEFAULT_INTERVAL)
        self._count = self._parse_int(args.get("count"), DEFAULT_COUNT)

        self._device.set_uwbcfg(
            CHAN=self._channel, TXCODE=self._pcode, RXCODE=self._pcode
        )
        # drop the board's config-set "ok" so it can't be misread as a beacon
        # event by whoever polls the port next once it's handed back (mirrors
        # SweepController._run_step's flush before firing the ranging command)
        self._device.session.flush_input()
        self._device.tcfm(
            chan=self._channel, pcode=self._pcode,
            count=self._count, interval=self._interval,
        )
        self._running = True

    def stop(self, args: dict) -> None:
        self._device.stop()
        self._running = False

    def status(self, args: dict) -> dict:
        return {
            "running": self._running,
            "channel": self._channel,
            "pcode": self._pcode,
            "interval": self._interval,
            "count": self._count,
        }
