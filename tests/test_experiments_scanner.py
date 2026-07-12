"""TDD (RED phase) for the active UWB ScannerController — the "nmap for UWB".

The scanner sweeps the PHY space (channels x preamble codes), and for each
combo actively polls with `initf`, listens a short window for a responder's
reply, and aggregates which combos produced a reply into a discovered-devices
model.

As with the repo's poll_once/board_loop split and webmodel.DetectorState, all
hardware/threads/time are kept OUT of the tested seam. The three pure pieces:

  1. ``sweep_plan(channels, pcodes) -> list[SweepStep]`` — the cartesian sweep.
  2. ``ScanResults`` — folds parser Events into discovered devices, with the
     clock INJECTED as an explicit ``timestamp`` argument to ``record`` so the
     model never calls real time and the tests stay deterministic.
  3. ``ScannerController`` — a start/stop/status controller (duck-typed for the
     experiments Dispatcher). It takes an already-detected Device and an
     injected ``now`` callable; sweeping is driven one combo per ``step()`` /
     the first combo on ``start()``, so no threads or sleeps are needed.

The module under test does not exist yet; the failing import IS the RED signal.
GREEN implements ``uwb_explorer/experiments/scanner.py`` to satisfy these tests.
"""

from __future__ import annotations

import json

from uwb_explorer.parser import Ack, ListenerFrame, RangeEntry, RangingResult
from tests.test_device import UWBCFG_REPLY, make_device, make_scripted

from uwb_explorer.experiments.scanner import (
    ScanResults,
    ScannerController,
    SweepStep,
    sweep_plan,
)


# ---- 1. the sweep plan -----------------------------------------------------

def test_sweep_plan_default_length_and_full_product():
    plan = sweep_plan()
    # defaults: channels (5, 9) x pcodes (9, 10, 11, 12)
    assert len(plan) == 2 * 4 == 8
    combos = [(s.channel, s.pcode) for s in plan]
    assert (5, 9) in combos and (9, 12) in combos
    # every channel/pcode pairing appears exactly once
    assert len(set(combos)) == 8


def test_sweep_plan_custom_args_are_the_cartesian_product_in_order():
    plan = sweep_plan(channels=(5, 9), pcodes=(9, 10))
    assert len(plan) == 4
    # channel is the OUTER loop, pcode the inner loop
    assert [(s.channel, s.pcode) for s in plan] == [
        (5, 9), (5, 10), (9, 9), (9, 10),
    ]


def test_sweep_step_carries_the_phy_combo():
    step = sweep_plan(channels=(9,), pcodes=(11,))[0]
    assert isinstance(step, SweepStep)
    assert step.channel == 9
    assert step.pcode == 11


# ---- 2. the discovery aggregation model ------------------------------------

def _ok_reply(addr="0x0001", status="Ok", distance_cm=42):
    return RangingResult(block=1, results=[
        RangeEntry(addr=addr, status=status, distance_cm=distance_cm)])


def test_ok_ranging_reply_becomes_a_discovery_for_that_combo():
    res = ScanResults()
    step = SweepStep(channel=5, pcode=9)
    res.record(step, _ok_reply(addr="0x0001"), timestamp=1000.0)

    devices = res.to_list()
    assert len(devices) == 1
    d = devices[0]
    assert d["addr"] == "0x0001"
    assert d["channel"] == 5
    assert d["pcode"] == 9
    assert d["reply_count"] == 1
    assert d["first_seen"] == 1000.0
    assert d["last_seen"] == 1000.0


def test_success_status_also_counts_and_non_ok_and_error_ack_do_not():
    res = ScanResults()
    step = SweepStep(channel=9, pcode=10)
    res.record(step, _ok_reply(addr="0x00AB", status="SUCCESS"), timestamp=1.0)
    # a non-Ok range entry is not a reply
    res.record(step, _ok_reply(addr="0xDEAD", status="Err"), timestamp=2.0)
    # an error Ack is not a reply either
    res.record(step, Ack(ok=False), timestamp=3.0)
    # a promiscuous sniff frame is not a reply to our poll
    res.record(step, ListenerFrame(payload=b"\x01", timestamp=0, offset=0),
               timestamp=4.0)

    devices = res.to_list()
    assert len(devices) == 1
    assert devices[0]["addr"] == "0x00AB"


def test_bare_ack_ok_is_not_a_discovery():
    # A bare "ok" Ack is the CLI acking our own `initf` command echo, NOT a
    # responder on the air (dl1): on hardware the initiator prints "ok" right
    # after `initf`, and counting it fabricated a phantom device on every scan.
    # A genuine responder shows up as a RangingResult (see the _ok_reply tests).
    res = ScanResults()
    step = SweepStep(channel=5, pcode=11)
    res.record(step, Ack(ok=True), timestamp=5.0)

    assert res.to_list() == []


def test_repeated_reply_same_combo_increments_count_and_updates_last_seen():
    res = ScanResults()
    step = SweepStep(channel=5, pcode=9)
    res.record(step, _ok_reply(addr="0x0001"), timestamp=100.0)
    res.record(step, _ok_reply(addr="0x0001"), timestamp=105.0)

    devices = res.to_list()
    assert len(devices) == 1  # not a duplicate entry
    assert devices[0]["reply_count"] == 2
    assert devices[0]["first_seen"] == 100.0
    assert devices[0]["last_seen"] == 105.0


def test_same_addr_on_a_different_combo_is_a_separate_device():
    res = ScanResults()
    res.record(SweepStep(5, 9), _ok_reply(addr="0x0001"), timestamp=1.0)
    res.record(SweepStep(9, 12), _ok_reply(addr="0x0001"), timestamp=2.0)

    devices = res.to_list()
    assert len(devices) == 2
    combos = {(d["channel"], d["pcode"]) for d in devices}
    assert combos == {(5, 9), (9, 12)}


def test_fresh_results_and_empty_window_yield_no_discovery():
    res = ScanResults()
    assert res.to_list() == []


def test_to_list_is_jsonable_with_the_expected_fields():
    res = ScanResults()
    res.record(SweepStep(5, 9), _ok_reply(addr="0x0001"), timestamp=1.0)
    dumped = json.dumps(res.to_list())  # must not raise
    d = json.loads(dumped)[0]
    for key in ("addr", "channel", "pcode", "reply_count",
                "first_seen", "last_seen"):
        assert key in d


# ---- 3. the ScannerController (start/stop/status) --------------------------

RANGING_REPLY = (
    b'{"Block":1,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":42}]}\r\n'
)


def _fixed_clock(t=1000.0):
    return lambda: t


def test_start_configures_the_first_combo_and_polls():
    # set_uwbcfg needs the board to answer the uwbcfg query
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = ScannerController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({})  # defaults -> first combo is channel 5, pcode 9

    tx = bytes(ser.tx)
    assert b"uwbcfg 5 " in tx               # PHY reconfigured for the combo
    assert b"initf -CHAN=5 -PCODE=9" in tx  # actively polls that combo
    assert dev.mode == "INITF"


def test_start_records_a_discovered_reply_into_status():
    # the board answers the uwbcfg query, and replies to our initf poll
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY, "initf": RANGING_REPLY})
    ctrl = ScannerController(dev, now=_fixed_clock(1000.0), dwell=0)
    ctrl.start({})

    devices = ctrl.status({})["devices"]
    assert len(devices) == 1
    d = devices[0]
    assert d["addr"] == "0x0001"
    assert d["channel"] == 5
    assert d["pcode"] == 9
    assert d["reply_count"] == 1
    assert d["first_seen"] == 1000.0


def test_stop_sends_stop_and_leaves_the_device_idle():
    dev, ser = make_device()
    ctrl = ScannerController(dev, now=_fixed_clock())
    ctrl.stop({})
    assert b"stop\r\n" in bytes(ser.tx)
    assert dev.mode == "STOP"
    assert ctrl.status({})["running"] is False


def test_status_reports_progress_and_a_jsonable_snapshot():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = ScannerController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({})

    st = ctrl.status({})
    assert st["total"] == 8           # default 2 channels x 4 pcodes
    assert st["step"] >= 1            # the first combo has been driven
    assert isinstance(st["devices"], list)
    json.dumps(st)                    # the whole status must be JSON-able


def test_blank_args_use_defaults_and_custom_args_are_honored():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = ScannerController(dev, now=_fixed_clock(), dwell=0)

    ctrl.start({})  # blank -> channels 5/9, pcodes 9-12
    assert ctrl.status({})["total"] == 8

    ctrl.start({"channels": "5,9", "pcodes": "9,10"})
    assert ctrl.status({})["total"] == 4


def test_step_drives_the_next_combo_without_threads():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = ScannerController(dev, now=_fixed_clock(), dwell=0)
    # two combos: (5, 9) then (9, 9)
    ctrl.start({"channels": "5,9", "pcodes": "9"})  # drives the first, (5, 9)

    assert ctrl.step() is True   # drives the second, (9, 9)
    tx = bytes(ser.tx)
    assert b"uwbcfg 9 " in tx
    assert b"initf -CHAN=9 -PCODE=9" in tx


def test_run_step_dwells_after_initf_so_late_replies_are_captured():
    # On hardware the responder's ranging blocks arrive ~200ms AFTER `initf`,
    # so a scan that drains immediately sees nothing. The step MUST dwell before
    # polling. Model that: the reply is delivered ONLY when the injected sleep
    # (the dwell) fires — with no dwell, no discovery.
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})  # NOTE: no initf reply
    def dwell_delivers(_secs):
        ser.feed(RANGING_REPLY)  # the responder answers during the dwell window
    ctrl = ScannerController(dev, now=_fixed_clock(1000.0),
                             sleep=dwell_delivers, dwell=0.5)
    ctrl.start({"channels": "9", "pcodes": "9"})

    devices = ctrl.status({})["devices"]
    assert len(devices) == 1
    assert devices[0]["addr"] == "0x0001"
    assert devices[0]["channel"] == 9


def test_run_step_sleeps_for_the_configured_dwell():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    slept: list[float] = []
    ctrl = ScannerController(dev, now=_fixed_clock(),
                             sleep=lambda s: slept.append(s), dwell=0.75)
    ctrl.start({"channels": "9", "pcodes": "9"})
    assert 0.75 in slept

    assert ctrl.step() is False  # sweep exhausted
