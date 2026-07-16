"""Pure-math tests for DW3000 antenna-delay calibration (bead uwb-qorvo-av8).

No hardware, no serial I/O — see docs/cli-protocol.md for why: ranging
distance (D_cm) floors near zero at point-blank range because the RX/TX
antenna delay baked into the timestamp math is uncalibrated. These tests
pin down the pure conversion arithmetic; tests/test_device.py covers
applying a calibrated value to a device over a fake serial seam.
"""

import pytest

from uwb_explorer.calibration import (
    DWT_TIME_UNITS_S,
    SPEED_OF_LIGHT_M_S,
    calibrate,
    calibrate_two_point,
    meters_to_ticks,
    round_trip_error_ticks,
    ticks_to_meters,
)


def test_constants_match_dw3000_datasheet():
    # DW3000 device time unit = 1 / (128 * 499.2 MHz) ~= 15.65 ps.
    assert SPEED_OF_LIGHT_M_S == 299_792_458.0
    assert DWT_TIME_UNITS_S == pytest.approx(15.650040064102564e-12, rel=1e-12)


def test_meters_to_ticks_and_back_round_trip():
    ticks = meters_to_ticks(1.234)
    assert ticks_to_meters(ticks) == pytest.approx(1.234, rel=1e-12)


def test_meters_to_ticks_one_metre():
    # 1 m of one-way light travel time, in device ticks.
    expected = 1.0 / SPEED_OF_LIGHT_M_S / DWT_TIME_UNITS_S
    assert meters_to_ticks(1.0) == pytest.approx(expected, rel=1e-12)
    assert meters_to_ticks(1.0) == pytest.approx(213.139, rel=1e-4)


def test_round_trip_error_ticks_is_double_the_one_way_ticks():
    # Δt(round trip) = 2Δd/c — the factor of 2 covers the two hops (out
    # and back) that a TWR round trip measurement actually contains.
    dd = 0.30
    assert round_trip_error_ticks(dd) == pytest.approx(2 * meters_to_ticks(dd), rel=1e-12)


def test_round_trip_error_ticks_30cm_over_read_exact_value():
    # Worked example from the bead: a 30 cm over-read.
    # Δt = 2·0.30 / 299792458 = 2.001438...e-9 s
    # Δticks = Δt / 15.650040064102564e-12 s ≈ 127.8836707760 ticks
    ticks = round_trip_error_ticks(0.30)
    assert ticks == pytest.approx(127.88367077600064, rel=1e-9)


def test_calibrate_zero_error_leaves_delay_unchanged():
    assert calibrate(true_m=1.0, measured_m=1.0, current_delay_ticks=16385) == 16385


def test_calibrate_30cm_over_read_worked_example():
    # measured is 30 cm LONGER than true -> antenna delay was too SMALL
    # (too little of the internal chip-to-antenna transit time was being
    # subtracted out), so the corrected delay must INCREASE.
    #
    # Per-register correction = round_trip_error_ticks(0.30) / 2
    #                          = 127.88367077600064 / 2
    #                          = 63.94183538800032 ticks
    # new = 16385 + 63.94183538800032 = 16448.941835388 -> round() = 16449
    new_delay = calibrate(true_m=1.0, measured_m=1.30, current_delay_ticks=16385)
    assert new_delay == 16449
    assert isinstance(new_delay, int)


def test_calibrate_30cm_under_read_is_the_mirror_image():
    # measured is 30 cm SHORTER than true -> delay was too LARGE -> decrease.
    new_delay = calibrate(true_m=1.0, measured_m=0.70, current_delay_ticks=16385)
    assert new_delay == 16321


def test_calibrate_over_and_under_read_are_symmetric_about_current():
    over = calibrate(true_m=1.0, measured_m=1.30, current_delay_ticks=16385)
    under = calibrate(true_m=1.0, measured_m=0.70, current_delay_ticks=16385)
    assert (over - 16385) == -(under - 16385)


def test_calibrate_scales_with_current_delay_offset():
    # The correction delta doesn't depend on the current_delay_ticks value
    # itself, only on the measured/true distance error.
    delta_a = calibrate(true_m=2.0, measured_m=2.10, current_delay_ticks=0) - 0
    delta_b = calibrate(true_m=2.0, measured_m=2.10, current_delay_ticks=20000) - 20000
    assert delta_a == delta_b


def test_calibrate_returns_python_int_for_cli_use():
    result = calibrate(true_m=0.5, measured_m=0.55, current_delay_ticks=16385)
    assert isinstance(result, int)


def test_calibrate_two_point_averages_two_single_point_corrections():
    # Two known-distance readings; two_point should land on the average of
    # the two *unrounded* per-register corrections, rounded once at the end
    # (not the average of two independently-rounded single-point results,
    # which would double-round and could disagree by a tick).
    delta_a = round_trip_error_ticks(1.30 - 1.0) / 2.0
    delta_b = round_trip_error_ticks(2.10 - 2.0) / 2.0
    expected = round(16385 + (delta_a + delta_b) / 2.0)
    result = calibrate_two_point(
        true_a_m=1.0, measured_a_m=1.30,
        true_b_m=2.0, measured_b_m=2.10,
        current_delay_ticks=16385,
    )
    assert result == expected


def test_calibrate_two_point_agrees_with_calibrate_when_points_are_identical():
    result = calibrate_two_point(
        true_a_m=1.0, measured_a_m=1.30,
        true_b_m=1.0, measured_b_m=1.30,
        current_delay_ticks=16385,
    )
    assert result == calibrate(true_m=1.0, measured_m=1.30, current_delay_ticks=16385)
