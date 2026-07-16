#!/usr/bin/env python3
"""Antenna-delay calibration for a DWM3001CDK (bead uwb-qorvo-av8).

HARDWARE SCRIPT — run this by hand on real boards. It is NOT invoked by the
test suite (uwb_explorer/calibration.py and Device.set_antenna_delay() are
covered by hardware-free unit tests instead; see tests/test_calibration.py
and tests/test_device.py).

Antenna delay compensates for the fixed internal transit time between the
DW3000's timestamp latch point and the physical antenna. Left at firmware
defaults it doesn't match a given board's actual PCB/antenna layout, which
biases every ranging measurement by a roughly constant offset — bad enough
at short range that the reported distance floors to ~0 (bead av8, see
uwb_explorer/parser.py's D_cm field and docs/cli-protocol.md §5).

What this script does:
  1. Waits for you to place the two boards at a precisely known distance.
  2. Starts ranging on THIS board (the peer board must already be running
     the opposite role — start it first, separately) and averages
     `--samples` successful D_cm readings.
  3. Feeds (true distance, averaged measured distance, current antenna
     delay) into uwb_explorer.calibration.calibrate() — or
     calibrate_two_point() if `--two-point` is given — to compute a
     corrected antenna-delay value.
  4. Applies it via Device.set_antenna_delay() and, unless `--no-save`,
     persists it to NVM with SAVE so it survives a power cycle.

Usage
-----
    # 1. On the PEER board, start the opposite ranging role first, e.g. over
    #    a raw terminal: `screen /dev/ttyACM0` then type `RESPF ...`
    #    (see docs/cli-protocol.md §3 for INITF/RESPF argument syntax).
    #
    # 2. Place the two boards at a precisely known distance (tape measure or
    #    a jig), clear line of sight, then run this on the board being
    #    calibrated:
    python tools/calibrate_antenna_delay.py --distance 1.0 --role initf

    # Two-point calibration (near + far placements averaged; reduces the
    # effect of one noisy/multipath-y placement):
    python tools/calibrate_antenna_delay.py --distance 0.5 --role initf --two-point 2.0

    # See the computed correction without writing it to the board:
    python tools/calibrate_antenna_delay.py --distance 1.0 --role initf --dry-run

    # Apply for this power-cycle only (skip NVM SAVE):
    python tools/calibrate_antenna_delay.py --distance 1.0 --role initf --no-save

    # Override the assumed CURRENT antenna-delay value (defaults to the
    # stock DW3000/QM33 SDK default, 16385 — this is a placeholder; check
    # your board's actual current value, e.g. via HELP ANTDELAY / DECA$ on
    # real hardware, before trusting the default):
    python tools/calibrate_antenna_delay.py --distance 1.0 --role initf --current-delay 16436

Caveats — read before running
------------------------------
- Device.set_antenna_delay() sends `ANTDELAY <tx> <rx>`. This exact CLI
  command name/argument order was NOT confirmed against a live board's HELP
  output while implementing bead av8 — docs/cli-protocol.md's researched
  command tables (§2) don't list an ANTDELAY entry for this firmware build.
  Run `HELP ANTDELAY` (or plain `HELP` and look for it) on the actual board
  FIRST. If the real command differs, fix the format string in
  uwb_explorer/device.py's set_antenna_delay() before relying on this script.
- --current-delay's default (16385) is a placeholder, not a value read off
  your board — this script has no documented way to *query* the presently
  programmed antenna delay (no such read-back command was found in the
  docs either). Track your board's actual current value externally
  (e.g. in this repo's docs) once you've confirmed it once.
- This script only understands the compact `{"Block":..,"D_cm":..}` ranging
  report (uwb_explorer/parser.py's RangingResult) — the SDK-1.1.x verbose
  `SESSION_INFO_NTF` text form (docs/cli-protocol.md §5a) is not parsed by
  this codebase yet. If your board prints that form instead, this script
  will see zero samples and time out.
- Keep line-of-sight clear and antenna orientation consistent between runs;
  multipath/NLOS makes individual D_cm samples noisy. Averaging `--samples`
  smooths sample noise but will not fix a bad/reflective placement.
- Does not touch hardware unless you actually run it.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

sys.path.insert(0, ".")

from uwb_explorer.calibration import calibrate, calibrate_two_point
from uwb_explorer.device import Device
from uwb_explorer.parser import RangingResult
from uwb_explorer.serialport import find_cli_port, open_cli

# Stock DW3000/QM33 SDK antenna-delay default — a PLACEHOLDER assumption,
# not verified against real hardware. See "Caveats" above.
DEFAULT_CURRENT_DELAY_TICKS = 16385


def collect_average_distance_cm(dev: Device, samples: int, timeout_s: float = 10.0) -> float:
    """Poll ranging reports (ranging must already be started) and return the
    mean D_cm across up to `samples` successful ("Ok"/"Success") readings.

    Raises RuntimeError if no successful reading arrives within timeout_s.
    """
    readings: list[int] = []
    t0 = time.time()
    while len(readings) < samples and time.time() - t0 < timeout_s:
        for ev in dev.poll_events():
            if isinstance(ev, RangingResult):
                for r in ev.results:
                    if r.status.lower() in ("ok", "success") and r.distance_cm is not None:
                        readings.append(r.distance_cm)
        time.sleep(0.05)
    if not readings:
        raise RuntimeError(
            f"No successful ranging reports in {timeout_s:.0f}s — check the "
            "peer board is running the opposite role and both boards agree "
            "on channel/PHY (UWBCFG)."
        )
    return statistics.mean(readings)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--distance", type=float, required=True,
        help="TRUE distance between the two boards' antennas, in metres.",
    )
    ap.add_argument(
        "--two-point", type=float, default=None,
        help="If given, also measure at this second TRUE distance (metres) "
             "and combine both readings via calibrate_two_point().",
    )
    ap.add_argument(
        "--role", choices=("initf", "respf"), default="initf",
        help="Ranging role to start on THIS board. The peer board must "
             "already be running the opposite role before you press Enter.",
    )
    ap.add_argument(
        "--samples", type=int, default=20,
        help="Successful ranging reports to average per distance point.",
    )
    ap.add_argument(
        "--current-delay", type=int, default=DEFAULT_CURRENT_DELAY_TICKS,
        help="Antenna-delay ticks presently programmed on this board "
             f"(default {DEFAULT_CURRENT_DELAY_TICKS} — a placeholder, see "
             "Caveats in this file's docstring).",
    )
    ap.add_argument("--port", default=None, help="Serial port; auto-detected if omitted.")
    ap.add_argument(
        "--no-save", action="store_true",
        help="Apply the new delay for this power-cycle only; skip SAVE (NVM persist).",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Compute and print the new delay but do not write it to the board.",
    )
    args = ap.parse_args()

    port = args.port or find_cli_port()
    if not port:
        print("No CLI port found (plug J20 native USB).", file=sys.stderr)
        return 1
    ser = open_cli(port)
    ser.setDTR(True)
    time.sleep(0.4)
    ser.reset_input_buffer()
    dev = Device(ser)
    if not dev.detect():
        print("Board not responding.", file=sys.stderr)
        return 1
    print(f"Board {dev.version} on {port}. Assuming current antenna delay = {args.current_delay} ticks.")

    def measure_at(true_m: float) -> float:
        input(f"\nPlace boards {true_m:.3f} m apart (peer already ranging), then press Enter...")
        dev.stop()
        dev.start_ranging(args.role)
        try:
            avg_cm = collect_average_distance_cm(dev, args.samples)
        finally:
            dev.stop()
        print(f"  {true_m:.3f} m true -> {avg_cm / 100:.4f} m measured "
              f"(avg over up to {args.samples} samples)")
        return avg_cm / 100.0

    measured_a = measure_at(args.distance)

    if args.two_point is not None:
        measured_b = measure_at(args.two_point)
        new_delay = calibrate_two_point(
            true_a_m=args.distance, measured_a_m=measured_a,
            true_b_m=args.two_point, measured_b_m=measured_b,
            current_delay_ticks=args.current_delay,
        )
    else:
        new_delay = calibrate(
            true_m=args.distance, measured_m=measured_a,
            current_delay_ticks=args.current_delay,
        )

    print(f"\nAntenna delay: {args.current_delay} -> {new_delay} "
          f"(delta {new_delay - args.current_delay:+d} ticks)")

    if args.dry_run:
        print("--dry-run given: not writing to the board.")
        return 0

    dev.set_antenna_delay(new_delay)
    print("Applied to the board's antenna-delay registers.")
    if args.no_save:
        print("Not saved (--no-save) — reverts on next power-cycle.")
    else:
        dev.session.send("save")
        print("Saved to NVM (auto-loads on next power-up).")

    print(
        "\nVerify: range again at a known distance (this script, --dry-run, "
        "or by eye) and confirm the reading now matches. Re-run — optionally "
        "with a different --distance / --current-delay — if it's still off."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
