"""Pure-math antenna-delay calibration for the DW3000 (bead uwb-qorvo-av8).

**Why:** ranging distance (`D_cm` / `distance[cm]`, see uwb_explorer/parser.py
and docs/cli-protocol.md §5) floors to ~0 at point-blank range because the
RX/TX antenna delay baked into the DW3000's TX/RX timestamps is uncalibrated
(left at firmware defaults, which assume a reference antenna/PCB layout that
doesn't exactly match a given board). Antenna delay represents the fixed
internal transit time between the chip's timestamp latch point and the
physical antenna; the firmware subtracts it out of every raw timestamp so
that only true air propagation time remains. If it's wrong, every measured
distance is off by a constant amount — at short range that constant can
exceed the true distance, driving the reported value to zero or negative
(which the firmware then likely clamps/floors).

**No hardware here.** Everything in this module is pure arithmetic — no
serial I/O, no device object. See uwb_explorer/device.py (`set_antenna_delay`)
for the thin method that applies a calibrated value to a device, and
tools/calibrate_antenna_delay.py for the human-run, hardware-touching script
that ties this module + that method together.

## Physics

The DW3000 system-time counter increments at the PRF chipping rate: one
device tick = 1 / (128 * 499.2 MHz) seconds ~= 15.65 ps (`DWT_TIME_UNITS` in
Qorvo's driver, unchanged from the DW1000 generation — see Qorvo/DecaWave
application note APS013 "DW1000 Antenna Delay Calibration", which the QM33
SDK carries forward for DW3000).

A DS-TWR/SS-TWR exchange computes a *one-way* distance from timestamps taken
during a *round trip* (poll out, response back). A one-way distance error
Δd (metres) between the reported and true distance therefore comes from a
round-trip time error of

    Δt_roundtrip = 2 * Δd / c            (c = SPEED_OF_LIGHT_M_S)

— the factor of 2 because the erroneous propagation estimate is counted once
on the way out and once on the way back before the ranging math halves it
into a one-way distance. In device ticks:

    Δticks_roundtrip = Δt_roundtrip / DWT_TIME_UNITS_S
                      = (2 * Δd / c) / DWT_TIME_UNITS_S

That round-trip error is attributable to THIS device's own antenna delay
(the peer's delay is assumed already correct/unchanged): once via the TX
antenna-delay register (when this device sends) and once via the RX
antenna-delay register (when this device receives the reply). Per the
standard Qorvo/DecaWave convention, a single calibrated delay value is
programmed into *both* the TX and RX antenna-delay registers (they share one
physical antenna and near-identical transit path), so the round-trip
correction is split evenly between the two registers:

    Δticks_per_register = Δticks_roundtrip / 2 = Δd / c / DWT_TIME_UNITS_S

(the round-trip factor of 2 and the "split between TX/RX" division of 2
cancel — the per-register correction is exactly the one-way light-time of
the distance error, in ticks).

**Sign:** a larger antenna-delay value causes the firmware to subtract MORE
transit time out of raw timestamps, which REDUCES reported distance. So an
over-read (measured > true, Δd > 0) needs the delay INCREASED, and this
module's convention (`error_m = measured_m - true_m`) makes that fall out
directly: `new_delay = current_delay + round(error_ticks_per_register)`,
no sign flip required.
"""

from __future__ import annotations

SPEED_OF_LIGHT_M_S = 299_792_458.0

# DW3000 device time unit: 1 / (128 * 499.2 MHz) seconds ~= 15.65 ps.
# This is the LSB of the 40-bit system time counter used for TX/RX
# timestamps and antenna-delay registers; unchanged from the DW1000
# generation (see Qorvo APS013).
DWT_TIME_UNITS_S = 1.0 / (128 * 499.2e6)


def meters_to_ticks(distance_m: float) -> float:
    """One-way distance (metres) -> device time ticks (unrounded)."""
    return distance_m / SPEED_OF_LIGHT_M_S / DWT_TIME_UNITS_S


def ticks_to_meters(ticks: float) -> float:
    """Device time ticks -> one-way distance (metres). Inverse of meters_to_ticks."""
    return ticks * DWT_TIME_UNITS_S * SPEED_OF_LIGHT_M_S


def round_trip_error_ticks(distance_error_m: float) -> float:
    """Distance error (measured - true, metres) -> round-trip time error, in ticks.

    Δt_roundtrip = 2 * Δd / c, converted to device ticks. This is the
    intermediate "total ticks of correction needed across the round trip"
    quantity described in the module docstring; `calibrate()` splits it in
    half between the TX and RX antenna-delay registers.
    """
    return 2.0 * distance_error_m / SPEED_OF_LIGHT_M_S / DWT_TIME_UNITS_S


def calibrate(true_m: float, measured_m: float, current_delay_ticks: int) -> int:
    """One-point antenna-delay calibration.

    Given a MEASURED distance reading (`measured_m`) taken while the two
    boards were actually `true_m` apart, and the antenna-delay value
    (`current_delay_ticks`) presently programmed into BOTH the TX and RX
    antenna-delay registers, return the corrected delay value to program
    into both registers so a repeat measurement at `true_m` would read
    `true_m` instead of `measured_m`.

    Rounds to the nearest integer tick (the CLI/register only accepts
    integers).
    """
    error_m = measured_m - true_m
    per_register_ticks = round_trip_error_ticks(error_m) / 2.0
    return round(current_delay_ticks + per_register_ticks)


def calibrate_two_point(
    true_a_m: float,
    measured_a_m: float,
    true_b_m: float,
    measured_b_m: float,
    current_delay_ticks: int,
) -> int:
    """Two-point antenna-delay calibration.

    Takes two independent (true, measured) distance pairs — e.g. a near and
    a far placement — and averages the correction each would suggest on its
    own, to reduce the effect of noise/multipath in any single reading.
    Falls back to plain `calibrate()` behaviour when the two points agree.
    """
    delta_a = calibrate(true_a_m, measured_a_m, current_delay_ticks) - current_delay_ticks
    delta_b = calibrate(true_b_m, measured_b_m, current_delay_ticks) - current_delay_ticks
    return round(current_delay_ticks + (delta_a + delta_b) / 2.0)
