#!/usr/bin/env python3
"""Live UWB sniffer / hunter for the DWM3001CDK.

Runs LISTENER (full-dump) and reports every frame heard, with MAC decode
and signal levels. Can sweep preamble codes / channels to lock onto a
signal whose exact PHY we don't know (e.g. Apple U1 precision finding).

    python tools/sniff.py                 # listen ch9 default, 30s
    python tools/sniff.py --seconds 60
    python tools/sniff.py --sweep         # cycle preamble codes 9-12
    python tools/sniff.py --channel 5

Coordinate with real traffic: start an AirTag "Find Nearby" (or any UWB
ranging) within ~1 m of the board while this runs.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, ".")

from uwb_explorer.device import Device
from uwb_explorer.mac import decode_frame
from uwb_explorer.parser import ListenerFrame
from uwb_explorer.serialport import find_cli_port, open_cli


def set_preamble(dev: Device, code: int) -> None:
    """Set TX+RX preamble codes together (UWBCFG positions 4 and 5)."""
    params = dev.get_uwbcfg()
    if not params:
        return
    params["TXCODE"] = code
    params["RXCODE"] = code
    from uwb_explorer.device import _UWBCFG_ORDER
    vals = " ".join(str(params[k]) for k in _UWBCFG_ORDER)
    dev.session.send(f"uwbcfg {vals}")
    time.sleep(0.3)


def listen_window(dev: Device, seconds: float, label: str) -> int:
    dev.stop(); time.sleep(0.2)
    dev.start_listener(full=True)
    print(f"[{label}] listening {seconds:.0f}s …", flush=True)
    count = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        for ev in dev.poll_events():
            if isinstance(ev, ListenerFrame):
                count += 1
                info = decode_frame(ev.payload)
                sig = f" rsl={ev.rssi_dbm}dBm fp={ev.first_path_dbm}" if ev.rssi_dbm is not None else ""
                print(f"  #{count:<4} {len(ev.payload):>3}B {info.frame_type:<12} "
                      f"src={info.src} dst={info.dst}{sig}")
                print(f"        {ev.payload.hex(' ')}")
        time.sleep(0.05)
    dev.stop()
    print(f"[{label}] {count} frames.", flush=True)
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--channel", type=int, default=None)
    ap.add_argument("--sweep", action="store_true", help="cycle preamble codes 9-12")
    ap.add_argument("--port", default=None)
    args = ap.parse_args()

    port = args.port or find_cli_port()
    if not port:
        print("No CLI port (plug J20 native USB).", file=sys.stderr)
        return 1
    ser = open_cli(port)
    ser.setDTR(True); time.sleep(0.4); ser.reset_input_buffer()
    dev = Device(ser)
    if not dev.detect():
        print("Board not responding — try replug or reset.", file=sys.stderr)
        return 1
    print(f"Board {dev.version} on {port}. Apps={dev.apps}")

    if args.channel:
        dev.stop(); dev.set_channel(args.channel)
        print(f"channel -> {args.channel}")

    total = 0
    try:
        if args.sweep:
            for code in (9, 10, 11, 12):
                set_preamble(dev, code)
                total += listen_window(dev, args.seconds / 4, f"pcode {code}")
        else:
            total = listen_window(dev, args.seconds, "listen")
    finally:
        dev.stop(); ser.close()
    print(f"\nTOTAL frames captured: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
