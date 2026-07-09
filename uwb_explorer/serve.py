"""Unified UWB service: one board loop feeding BOTH the web dashboard and BLE.

Running the web server and the BLE peripheral as separate processes would make
them fight over the board's single serial port. Instead this owns one
DetectorState fed by a single board_loop, then exposes it via the phone web
dashboard (a thread) and the BLE peripheral (asyncio in the main thread).

Run (as root, for BlueZ):  ./venv/bin/python -m uwb_explorer.serve [--sweep]
    --no-ble   web dashboard only
    --no-web   BLE only
"""

from __future__ import annotations

import argparse
import sys
import threading

from .webmodel import DetectorState
from .web import board_loop, DashboardServer
from .experiments.control import Dispatcher, EXPERIMENTS
from .experiments.scanner import ScannerController
from .experiments.transponder import TransponderController


class _PlaceholderController:
    """Provisional stand-in until the real per-experiment controllers land.

    Accepts the start/stop/status downlink so the web hub is fully wired now,
    but does nothing on the board yet. Real controllers arrive in beads
    .5 (scanner) / .8 (transponder) / .11 (beacon) / .16 (fuzzer); the
    half-duplex pause-the-board-loop handoff is refined there too.
    """

    def __init__(self, exp: str):
        self._exp = exp

    def start(self, args):
        return {"ok": True, "exp": self._exp, "note": "controller not yet implemented"}

    def stop(self, args):
        return {"ok": True, "exp": self._exp, "note": "controller not yet implemented"}

    def status(self, args):
        return {"exp": self._exp, "phase": "unimplemented"}


def _provisional_dispatcher() -> Dispatcher:
    """A dispatcher with a placeholder controller per known experiment letter."""
    return Dispatcher({letter: _PlaceholderController(letter) for letter in EXPERIMENTS})


def build_dispatcher(device) -> Dispatcher:
    """Wire the REAL scanner + transponder controllers, placeholders for the rest.

    The scanner (bead .5/.6) drives a live ``ScannerController(device)`` on "S"
    that actively sweeps the PHY space; the transponder (bead .8/.9) drives a
    live ``TransponderController(device)`` on "T" that answers polls across the
    same space. B/Z keep the provisional placeholder until their controllers
    land, so the web hub stays fully wired and no letter crashes when driven.
    """
    registry: dict[str, object] = {
        "S": ScannerController(device),
        "T": TransponderController(device),
    }
    for letter in EXPERIMENTS:
        if letter not in registry:
            registry[letter] = _PlaceholderController(letter)
    return Dispatcher(registry)


class _LazyDispatcher:
    """Builds the real dispatcher once the board Device is live.

    The board Device is created *inside* board_loop (it owns the single serial
    port), so it doesn't exist when the DashboardServer is constructed. This
    proxy holds the live device and lazily builds ``build_dispatcher(dev)`` on
    the first command; until a board connects it falls back to the provisional
    placeholder dispatcher so the downlink never crashes.

    FOLLOW-UP (hardware-verified, intentionally NOT implemented here): driving
    a scanner actively polls the SAME serial port the passive board_loop is
    listening on. Real half-duplex arbitration — pausing the listener loop and
    handing the port to the experiment, then resuming — is a deliberate
    follow-up. This proxy only makes the seam honest; it does not serialize the
    two owners of the port.
    """

    def __init__(self):
        self._device = None
        self._real: Dispatcher | None = None
        self._provisional = _provisional_dispatcher()

    def set_device(self, device) -> None:
        # board_loop calls this on (re)connect with the live device, or None on
        # disconnect; rebuild the real dispatcher against the new device.
        self._device = device
        self._real = None

    def dispatch(self, cmd):
        if self._device is None:
            return self._provisional.dispatch(cmd)
        if self._real is None:
            self._real = build_dispatcher(self._device)
        return self._real.dispatch(cmd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="UWB unified web+BLE service")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--sweep", action="store_true",
                    help="cycle preamble codes 9-12 to hear more device types")
    ap.add_argument("--no-web", action="store_true")
    ap.add_argument("--no-ble", action="store_true")
    args = ap.parse_args(argv)

    state = DetectorState()
    stop = threading.Event()

    # the real scanner controller needs the board Device, which board_loop
    # creates as it owns the single serial port; this proxy receives the live
    # device via on_connect and lazily builds build_dispatcher(dev). Live
    # board-loop pause/resume arbitration remains a flagged follow-up (see
    # _LazyDispatcher).
    dispatcher = _LazyDispatcher()

    # one board loop feeds everything
    threading.Thread(target=board_loop, args=(state, stop),
                     kwargs={"sweep": args.sweep, "on_connect": dispatcher.set_device},
                     daemon=True).start()

    if not args.no_web:
        srv = DashboardServer(state.snapshot, host=args.host, port=args.port,
                              dispatcher=dispatcher)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"web dashboard on http://{args.host}:{args.port}", file=sys.stderr)

    if args.no_ble:
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
        stop.set()
        return 0

    # BLE runs in the main thread (bluezero's publish() drives the GLib loop)
    from .ble import run_ble
    try:
        run_ble(state, interval=args.interval)
    except KeyboardInterrupt:
        pass
    except Exception as e:  # never let a BLE failure kill the web dashboard
        print(f"BLE peripheral error: {e!r} — web dashboard stays up", file=sys.stderr)
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
