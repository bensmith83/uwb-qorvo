#!/usr/bin/env python3
"""Try to capture RAW over-the-air frame bytes from an Apple UWB session.

Our first capture threw PHY-header errors: the listener (SP0, expecting a
normal header) couldn't parse Apple's STS frames. This sweeps the STS mode
and SFD type on the preamble codes that responded (10/11), running the
FULL-DUMP listener, and reports any frame the radio actually dumps (LSTN).

Best case: a control frame is received and we get real ciphertext bytes.
Likely case: pure SP3 STS traffic → detection only, no dumpable bytes.

    python tools/bytehunt.py --seconds 60
Keep an AirTag "Find Nearby" session active next to the board throughout.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, ".")

from uwb_explorer.device import Device, _UWBCFG_ORDER
from uwb_explorer.serialport import find_cli_port, open_cli

# (preamble_code, STSMODE, SFDTYPE) combinations to try.
# STSMODE: 0=SP0(no STS) 1=SP1 2=SP2 3=SP3 ; SFDTYPE: 8-symbol variants.
COMBOS = [
    (10, 1, 3), (10, 3, 3), (10, 1, 2),
    (11, 1, 3), (11, 3, 3), (11, 3, 2),
]


def apply_cfg(dev: Device, code: int, stsmode: int, sfdtype: int) -> None:
    dev.stop(); time.sleep(0.2)
    p = dev.get_uwbcfg() or {}
    p["CHAN"] = 9; p["TXCODE"] = code; p["RXCODE"] = code
    p["STSMODE"] = stsmode; p["SFDTYPE"] = sfdtype
    dev.session.send("uwbcfg " + " ".join(str(p[k]) for k in _UWBCFG_ORDER))
    time.sleep(0.3)


def hunt(dev: Device, ser, seconds: float) -> list[str]:
    """Full-dump listen; return any raw LSTN frame lines seen."""
    frames: list[str] = []
    dev.stop(); time.sleep(0.15)
    ser.reset_input_buffer()
    dev.session.send("listener2 1")
    buf = bytearray(); t0 = time.time()
    while time.time() - t0 < seconds:
        chunk = ser.read(ser.in_waiting or 1)
        if not chunk:
            time.sleep(0.02); continue
        buf.extend(chunk)
        while b"\n" in buf:
            line, _, rest = buf.partition(b"\n"); buf[:] = rest
            txt = line.decode("utf-8", "replace").strip()
            if "LSTN" in txt:
                frames.append(txt)
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--port", default=None)
    args = ap.parse_args()
    port = args.port or find_cli_port()
    if not port:
        print("No CLI port.", file=sys.stderr); return 1
    ser = open_cli(port); ser.setDTR(True); time.sleep(0.4); ser.reset_input_buffer()
    dev = Device(ser)
    if not dev.detect():
        print("Board not responding.", file=sys.stderr); return 1
    per = args.seconds / len(COMBOS)
    print(f"Board {dev.version}. Hunting raw bytes across {len(COMBOS)} PHY combos "
          f"({per:.0f}s each). Keep the AirTag ranging.\n")
    caught: list[str] = []
    for code, sts, sfd in COMBOS:
        apply_cfg(dev, code, sts, sfd)
        # confirm the RX PHY events still fire (proves we're on Apple's signal)
        base = dev.get_lstat() or {}
        frames = hunt(dev, ser, per)
        cur = dev.get_lstat() or {}
        d_evt = sum(max(0, cur.get(k, 0) - base.get(k, 0))
                    for k in ("SFDD", "PHE", "CRCB", "CRCG", "SFDTO"))
        tag = f"pcode{code} STS{sts} SFD{sfd}"
        print(f"  [{tag}] activity={d_evt:5d}  frames_dumped={len(frames)}")
        for fr in frames[:3]:
            print(f"      >> {fr[:120]}")
        caught.extend(frames)
    dev.stop(); ser.close()
    print(f"\nTOTAL raw frames dumped: {len(caught)}")
    if caught:
        print("SUCCESS — real over-the-air bytes captured (see above).")
    else:
        print("No dumpable frames — consistent with pure SP3 STS ranging "
              "(encrypted core is never exposed as payload bytes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
