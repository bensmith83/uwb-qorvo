"""TDD (RED phase) for the stock-firmware BeaconController — a periodic
fixed-frame TX beacon (uwb-qorvo-1hu.11).

Unlike ScannerController/TransponderController (uwb_explorer.experiments.sweep),
the beacon does NOT sweep the PHY space and plays no ranging role: it holds ONE
fixed channel/preamble-code combo and drives stock firmware's TCFM ("Test
Continuous Frame Mode", ``Device.tcfm``) to transmit fixed test frames — no new
firmware, no ``initf``/``respf``. The FIRMWARE owns the actual periodicity via
TCFM's own ``count``/``interval`` arguments, so — unlike the sweep controllers'
injected dwell/sleep that drives combo-to-combo stepping on the Pi side — the
beacon needs no injected clock/sleep and no ``step()``: ``start()`` configures
the board once and fires ONE ``tcfm`` call.

As with the other controllers, BeaconController is a plain start/stop/status
controller duck-typed for uwb_explorer.experiments.control.Dispatcher.

The module under test does not exist yet; the failing import IS the RED signal.
GREEN implements ``uwb_explorer/experiments/beacon.py`` to satisfy these tests.
"""

from __future__ import annotations

import json

from tests.test_device import UWBCFG_REPLY, make_device, make_scripted

from uwb_explorer.experiments.beacon import (
    DEFAULT_CHANNEL,
    DEFAULT_COUNT,
    DEFAULT_INTERVAL,
    DEFAULT_PCODE,
    BeaconController,
)


def test_start_configures_the_board_with_default_combo_and_fires_tcfm():
    # set_uwbcfg needs the board to answer the uwbcfg query
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = BeaconController(dev)
    ctrl.start({})  # defaults

    tx = bytes(ser.tx)
    assert b"uwbcfg 5 " in tx   # PHY reconfigured for the default combo (chan 5)
    assert b"tcfm" in tx        # begins the fixed-frame TX beacon
    assert dev.mode == "TCFM"


def test_start_honors_custom_channel_pcode_and_interval():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = BeaconController(dev)
    ctrl.start({"channel": "9", "pcode": "12", "interval": "2.5"})

    tx = bytes(ser.tx)
    assert b"uwbcfg 9 " in tx
    assert b"tcfm" in tx
    st = ctrl.status({})
    assert st["channel"] == 9
    assert st["pcode"] == 12
    assert st["interval"] == 2.5


def test_start_sends_the_configured_count_and_interval_to_tcfm():
    dev, ser = make_device()
    ctrl = BeaconController(dev)
    ctrl.start({"interval": "5", "count": "100"})

    # Device.tcfm renders "tcfm {count} {interval}" onto the wire when both
    # are given (see tests/test_device.py::test_tcfm_count_and_interval_positional)
    assert b"tcfm 100 5.0\r\n" in bytes(ser.tx)


def test_blank_args_use_the_documented_defaults():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = BeaconController(dev)
    ctrl.start({})

    st = ctrl.status({})
    assert st["channel"] == DEFAULT_CHANNEL
    assert st["pcode"] == DEFAULT_PCODE
    assert st["interval"] == DEFAULT_INTERVAL
    assert st["count"] == DEFAULT_COUNT


def test_status_reports_running_true_after_start():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = BeaconController(dev)
    assert ctrl.status({})["running"] is False   # not started yet

    ctrl.start({})
    st = ctrl.status({})
    assert st["running"] is True
    json.dumps(st)   # the whole status must be JSON-able


def test_stop_sends_stop_and_clears_running():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = BeaconController(dev)
    ctrl.start({})
    assert ctrl.status({})["running"] is True

    ctrl.stop({})
    assert b"stop\r\n" in bytes(ser.tx)
    assert dev.mode == "STOP"
    assert ctrl.status({})["running"] is False


def test_stop_without_a_prior_start_does_not_crash():
    dev, ser = make_device()
    ctrl = BeaconController(dev)
    ctrl.stop({})
    assert b"stop\r\n" in bytes(ser.tx)
    assert ctrl.status({})["running"] is False


def test_restarting_with_new_args_updates_status():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = BeaconController(dev)
    ctrl.start({"channel": "5", "pcode": "9"})
    ctrl.start({"channel": "9", "pcode": "10", "interval": "1.5"})

    st = ctrl.status({})
    assert st["channel"] == 9
    assert st["pcode"] == 10
    assert st["interval"] == 1.5
    assert st["running"] is True


def test_start_flushes_the_config_ack_before_tcfm():
    # mirrors SweepController._run_step: the board's config-set "ok" must not
    # be left sitting in the input buffer for whoever polls the port next
    # (the arbiter's resumed listener) once the beacon hands the port back.
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = BeaconController(dev)
    ctrl.start({})

    # nothing buffered for poll_events to (mis)report as a beacon event
    assert list(dev.poll_events()) == []
