"""Minimal interactive console for the DWM3001CDK CLI firmware.

Usage:
    python -m uwb_explorer.console [--port /dev/ttyACM1]

A raw REPL: whatever you type is sent to the board; everything the board
emits is printed (with JSxxxx length-prefixed blocks parsed and pretty-
printed when recognised). Ctrl-C or 'quit' exits. Good for first-contact
sanity checks before the full TUI.
"""

from __future__ import annotations

import argparse
import sys
import threading

from .parser import Ack, InfoBlock, ListenerFrame, RangingResult, parse_line
from .serialport import find_cli_port, open_cli


def _reader(ser, stop_event: threading.Event) -> None:
    buf = bytearray()
    while not stop_event.is_set():
        chunk = ser.read(256)
        if not chunk:
            continue
        buf.extend(chunk)
        while b"\n" in buf:
            raw, _, rest = buf.partition(b"\n")
            buf[:] = rest
            line = raw.decode("utf-8", "replace").strip()
            if line:
                _print_line(line)


def _print_line(line: str) -> None:
    ev = parse_line(line)
    if isinstance(ev, RangingResult):
        for r in ev.results:
            d = f"{r.distance_cm} cm" if r.distance_cm is not None else "--"
            print(f"  [RANGE] blk {ev.block} {r.addr} {r.status} {d} "
                  f"AoA {r.aoa_azimuth_deg}")
    elif isinstance(ev, ListenerFrame):
        extra = f" rsl {ev.rssi_dbm}" if ev.rssi_dbm is not None else ""
        print(f"  [FRAME] {len(ev.payload)}B {ev.payload.hex(' ')} "
              f"ts {ev.timestamp:#x}{extra}")
    elif isinstance(ev, InfoBlock):
        print(f"  [INFO] {ev.data}")
    elif isinstance(ev, Ack):
        print(f"  [{'ok' if ev.ok else 'ERR'}]")
    else:
        print(f"  · {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    args = ap.parse_args(argv)

    port = args.port or find_cli_port()
    if port is None:
        print("No CLI port found (plug into J20 native-USB). Ports seen: "
              "run `python -m serial.tools.list_ports -v`.", file=sys.stderr)
        return 1
    print(f"Connected to {port}. Type commands (help, stat, listener2, "
          f"stop, initf, respf). Ctrl-C to exit.")
    ser = open_cli(port)
    stop_event = threading.Event()
    t = threading.Thread(target=_reader, args=(ser, stop_event), daemon=True)
    t.start()
    try:
        for line in sys.stdin:
            cmd = line.strip()
            if cmd in ("quit", "exit"):
                break
            ser.write(cmd.encode() + b"\r\n")
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        ser.write(b"stop\r\n")
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
