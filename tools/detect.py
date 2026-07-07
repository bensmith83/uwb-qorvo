#!/usr/bin/env python3
"""UWB energy detector for the DWM3001CDK.

Rather than trying to DECODE frames (impossible for encrypted Apple STS
traffic), this polls the listener's PHY event counters (LSTAT) and reports
when they climb — proof that UWB frames are hitting the antenna even if we
can't read them. Great for "did we hear the iPhone / AirTag" experiments.

    python tools/detect.py --seconds 45
    python tools/detect.py --seconds 45 --channel 5

Watched counters:
  SFDD  = SFD detections   (a UWB frame's start-of-frame was seen — strongest)
  PHE   = PHY header errors (frame started, header undecodable — e.g. encrypted)
  CRCB  = bad CRC           (frame received, integrity failed — e.g. encrypted)
  CRCG  = good CRC          (fully decoded frame)
  SFDTO/PTO = preamble seen but timed out
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, ".")

from uwb_explorer.device import Device
from uwb_explorer.serialport import find_cli_port, open_cli

WATCH = ["SFDD", "PHE", "CRCB", "CRCG", "SFDTO", "PTO", "ARFE", "STSE"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=45)
    ap.add_argument("--channel", type=int, default=None)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--sweep", action="store_true",
                    help="cycle preamble codes 9-12 to find an unknown PHY")
    ap.add_argument("--codes", default="9,10,11,12",
                    help="comma-separated preamble codes to sweep")
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
        print("Board not responding.", file=sys.stderr)
        return 1
    if args.channel:
        dev.stop(); dev.set_channel(args.channel)
    cfg = dev.get_uwbcfg() or {}
    print(f"Board {dev.version} | channel {cfg.get('CHAN')} "
          f"pcode {cfg.get('TXCODE')} | detecting {args.seconds:.0f}s")
    print("Trigger UWB now (AirTag Find Nearby / ranging) near the board.\n")

    codes = [int(c) for c in args.codes.split(",")] if args.sweep else [cfg.get("TXCODE", 9)]
    window = args.seconds / len(codes)
    peak = dict.fromkeys(WATCH, 0)
    from uwb_explorer.device import _UWBCFG_ORDER

    for code in codes:
        if args.sweep:
            dev.stop(); time.sleep(0.2)
            p = dev.get_uwbcfg() or {}
            p["TXCODE"] = code; p["RXCODE"] = code
            dev.session.send("uwbcfg " + " ".join(str(p[k]) for k in _UWBCFG_ORDER))
            time.sleep(0.3)
        dev.stop(); time.sleep(0.2)
        dev.start_listener()
        base = dev.get_lstat() or {}
        label = f"pcode {code}"
        t0 = time.time()
        while time.time() - t0 < window:
            time.sleep(args.interval)
            cur = dev.get_lstat()
            if not cur:
                continue
            delta = {k: cur.get(k, 0) - base.get(k, 0) for k in WATCH}
            active = {k: v for k, v in delta.items() if v}
            for k in WATCH:
                peak[k] = max(peak[k], delta[k])
            if active:
                print(f"  [{label}] t+{time.time()-t0:4.0f}s  " +
                      "  ".join(f"{k}+{v}" for k, v in active.items()))
    dev.stop(); ser.close()

    print("\n=== summary (max counts seen over baseline) ===")
    for k in WATCH:
        print(f"  {k:6} {peak[k]}")
    detected = peak["SFDD"] or peak["PHE"] or peak["CRCB"] or peak["CRCG"]
    if detected:
        print("\n*** UWB ENERGY DETECTED — frames hit the antenna. ***")
        if peak["CRCG"]:
            print("    Some frames fully decoded (good CRC).")
        elif peak["PHE"] or peak["CRCB"]:
            print("    Frames received but undecodable — consistent with "
                  "encrypted / foreign-PHY traffic (e.g. Apple U1).")
    else:
        print("\nNo UWB energy detected on this channel/config.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
