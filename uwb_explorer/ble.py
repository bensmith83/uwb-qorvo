"""BLE peripheral that streams UWB detector state to a phone (e.g. the iOS app).

Reuses the existing board loop (uwb_explorer.web.board_loop) to keep a
DetectorState fed from the DWM3001CDK, and exposes it as a single GATT
characteristic (read + notify) carrying the compact JSON from blecodec. This
lets an iPhone receive live UWB data over Bluetooth with no WiFi.

Uses `bluezero` (the BlueZ D-Bus bindings), which registers advertisements
BlueZ accepts — `bless` sent parameters BlueZ 5.82 rejected.

Run (as root, for BlueZ):  ./venv/bin/python -m uwb_explorer.ble [--sweep]
"""

from __future__ import annotations

import argparse
import sys
import threading

from .blecodec import encode_state
from .webmodel import DetectorState

# Custom 128-bit UUIDs — the iOS app scans for SERVICE_UUID and subscribes to CHAR_UUID.
SERVICE_UUID = "6e5f0001-b5a3-f393-e0a9-e50e24dcca9e"
CHAR_UUID = "6e5f0002-b5a3-f393-e0a9-e50e24dcca9e"
# Keep the advertised name short so name + 128-bit UUID fit the 31-byte packet.
DEVICE_NAME = "UWB"


def build_read_handler(state: DetectorState):
    """Return a read callback yielding the current payload as a list of bytes
    (bluezero wants a list; bytes(...) of it still works in tests)."""
    def read(*args, **kwargs):
        return list(encode_state(state.snapshot()))
    return read


class _Notifier:
    """Pushes the latest state to subscribed centrals on a timer."""
    def __init__(self, state: DetectorState, interval_ms: int):
        self.state = state
        self.interval_ms = interval_ms
        self._char = None

    def on_notify(self, notifying, characteristic):
        self._char = characteristic
        if notifying:
            from bluezero import async_tools
            async_tools.add_timer_ms(self.interval_ms, self._tick)

    def _tick(self):
        if self._char is None:
            return False  # stop the timer
        self._char.set_value(list(encode_state(self.state.snapshot())))
        return True  # keep firing


def run_ble(state: DetectorState, name: str = DEVICE_NAME, interval: float = 0.5) -> None:
    # Imported lazily so the module (and its unit tests) load without bluezero.
    from bluezero import adapter, peripheral

    address = list(adapter.Adapter.available())[0].address
    notifier = _Notifier(state, int(interval * 1000))

    p = peripheral.Peripheral(address, local_name=name)
    p.add_service(srv_id=1, uuid=SERVICE_UUID, primary=True)
    p.add_characteristic(
        srv_id=1, chr_id=1, uuid=CHAR_UUID,
        value=list(encode_state(state.snapshot())), notifying=False,
        flags=["read", "notify"],
        read_callback=build_read_handler(state),
        notify_callback=notifier.on_notify,
    )
    print(f"BLE peripheral '{name}' advertising service {SERVICE_UUID}", file=sys.stderr)
    p.publish()  # blocks in the GLib main loop


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="UWB BLE peripheral for the iOS app")
    ap.add_argument("--sweep", action="store_true",
                    help="cycle preamble codes 9-12 to hear more device types")
    ap.add_argument("--interval", type=float, default=0.5,
                    help="seconds between BLE notifications")
    ap.add_argument("--name", default=DEVICE_NAME)
    args = ap.parse_args(argv)

    from .web import board_loop  # lazy: pulls in serial only when actually running

    state = DetectorState()
    stop = threading.Event()
    threading.Thread(target=board_loop, args=(state, stop),
                     kwargs={"sweep": args.sweep}, daemon=True).start()
    try:
        run_ble(state, name=args.name, interval=args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
