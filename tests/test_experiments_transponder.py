"""TDD (RED phase) for the permissive UWB TransponderController.

The transponder is the mirror image of the ScannerController: instead of
actively polling with ``initf`` and folding replies, it cycles the same PHY
space (channels x preamble codes) driving ``respf`` so the board ANSWERS polls
from unknown initiators, and aggregates the answered polls (initiator addr,
count, distance/rssi) into a report — a "discoverable UWB landmark".

As with the scanner, all hardware/threads/real time are kept OUT of the tested
seam. The three pure pieces mirrored here:

  1. ``config_plan(channels, pcodes) -> list[SweepStep]`` — the cartesian cycle
     of PHY combos to answer on (channel outer, pcode inner). We REUSE
     ``scanner.SweepStep`` — a PHY combo is a PHY combo whether we poll or
     answer on it.
  2. ``TransponderResults`` — folds parser Events into an ANSWERED-POLLS report
     keyed by (initiator addr, combo), with the clock INJECTED as an explicit
     ``timestamp`` argument to ``record`` so the model never calls real time.
  3. ``TransponderController`` — a start/stop/status controller (duck-typed for
     the experiments Dispatcher). It takes an already-detected Device and an
     injected ``now`` callable; the cycle is driven one combo per ``step()`` /
     the first combo on ``start()``, so no threads or sleeps are needed.

The module under test does not exist yet; the failing import IS the RED signal.
GREEN implements ``uwb_explorer/experiments/transponder.py`` to satisfy these
tests.
"""

from __future__ import annotations

import json

import pytest

from uwb_explorer.parser import Ack, ListenerFrame, RangeEntry, RangingResult
from tests.test_device import UWBCFG_REPLY, make_device, make_scripted

from uwb_explorer.experiments.scanner import SweepStep
from uwb_explorer.experiments.transponder import (
    TransponderController,
    TransponderResults,
    config_plan,
)


# ---- 1. the config plan ----------------------------------------------------

def test_config_plan_default_length_and_full_product():
    plan = config_plan()
    # defaults: channels (5, 9) x pcodes (9, 10, 11, 12)
    assert len(plan) == 2 * 4 == 8
    combos = [(s.channel, s.pcode) for s in plan]
    assert (5, 9) in combos and (9, 12) in combos
    # every channel/pcode pairing appears exactly once
    assert len(set(combos)) == 8


def test_config_plan_custom_args_are_the_cartesian_product_in_order():
    plan = config_plan(channels=(5, 9), pcodes=(9, 10))
    assert len(plan) == 4
    # channel is the OUTER loop, pcode the inner loop
    assert [(s.channel, s.pcode) for s in plan] == [
        (5, 9), (5, 10), (9, 9), (9, 10),
    ]
    # combos reuse the scanner's PHY-combo type
    assert all(isinstance(s, SweepStep) for s in plan)


# ---- 2. the answered-polls aggregation model -------------------------------

def _poll(addr="0x0001", status="Ok", distance_cm=42):
    return RangingResult(block=1, results=[
        RangeEntry(addr=addr, status=status, distance_cm=distance_cm)])


def test_ok_ranging_poll_becomes_an_answered_record_for_that_combo():
    res = TransponderResults()
    step = SweepStep(channel=5, pcode=9)
    res.record(step, _poll(addr="0x0001", distance_cm=42), timestamp=1000.0)

    answered = res.to_list()
    assert len(answered) == 1
    a = answered[0]
    assert a["addr"] == "0x0001"
    assert a["channel"] == 5
    assert a["pcode"] == 9
    assert a["poll_count"] == 1
    assert a["first_seen"] == 1000.0
    assert a["last_seen"] == 1000.0
    assert a["distance_cm"] == 42


def test_success_status_counts_and_non_ok_and_error_ack_and_frame_do_not():
    res = TransponderResults()
    step = SweepStep(channel=9, pcode=10)
    res.record(step, _poll(addr="0x00AB", status="SUCCESS"), timestamp=1.0)
    # a non-Ok range entry is not an answered poll
    res.record(step, _poll(addr="0xDEAD", status="Err"), timestamp=2.0)
    # an error Ack is not an answered poll either
    res.record(step, Ack(ok=False), timestamp=3.0)
    # a promiscuous sniff frame is not an answered poll
    res.record(step, ListenerFrame(payload=b"\x01", timestamp=0, offset=0),
               timestamp=4.0)

    answered = res.to_list()
    assert len(answered) == 1
    assert answered[0]["addr"] == "0x00AB"


def test_ack_ok_counts_as_one_answered_poll_with_sentinel_addr():
    res = TransponderResults()
    step = SweepStep(channel=5, pcode=11)
    res.record(step, Ack(ok=True), timestamp=5.0)

    answered = res.to_list()
    assert len(answered) == 1
    a = answered[0]
    assert a["channel"] == 5
    assert a["pcode"] == 11
    assert a["poll_count"] == 1
    # an anonymous "ok" answer has no source address -> sentinel
    assert a["addr"] == "ok"


def test_repeated_poll_same_combo_increments_count_and_updates_last_seen():
    res = TransponderResults()
    step = SweepStep(channel=5, pcode=9)
    res.record(step, _poll(addr="0x0001"), timestamp=100.0)
    res.record(step, _poll(addr="0x0001"), timestamp=105.0)

    answered = res.to_list()
    assert len(answered) == 1  # not a duplicate record
    a = answered[0]
    assert a["poll_count"] == 2
    assert a["first_seen"] == 100.0   # fixed at first sight
    assert a["last_seen"] == 105.0    # advanced on the repeat


def test_same_addr_on_a_different_combo_is_a_separate_record():
    res = TransponderResults()
    res.record(SweepStep(5, 9), _poll(addr="0x0001"), timestamp=1.0)
    res.record(SweepStep(9, 12), _poll(addr="0x0001"), timestamp=2.0)

    answered = res.to_list()
    assert len(answered) == 2
    combos = {(a["channel"], a["pcode"]) for a in answered}
    assert combos == {(5, 9), (9, 12)}


def test_fresh_results_report_nothing():
    res = TransponderResults()
    assert res.to_list() == []


def test_to_list_is_jsonable_with_the_expected_fields():
    res = TransponderResults()
    res.record(SweepStep(5, 9), _poll(addr="0x0001"), timestamp=1.0)
    dumped = json.dumps(res.to_list())  # must not raise
    a = json.loads(dumped)[0]
    for key in ("addr", "channel", "pcode", "poll_count",
                "first_seen", "last_seen", "distance_cm"):
        assert key in a


# ---- 3. the TransponderController (start/stop/status) -----------------------

# a scripted initiator polling us; respf replies with a RangingResult line
RESPF_REPLY = (
    b'{"Block":1,"results":[{"Addr":"0x00AB","Status":"Ok","D_cm":77}]}\r\n'
)


def _fixed_clock(t=1000.0):
    return lambda: t


def test_start_configures_the_first_combo_and_answers():
    # set_uwbcfg needs the board to answer the uwbcfg query
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    # dwell=0: this test only cares about wiring, not the dwell/repeated-poll
    # behavior (see the dedicated dwell tests below) — dwell=0 keeps it
    # instant now that dwell is a real injected parameter (shared base,
    # bead 1hu.20), same convention the scanner tests already use.
    ctrl = TransponderController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({})  # defaults -> first combo is channel 5, pcode 9

    tx = bytes(ser.tx)
    assert b"uwbcfg 5 " in tx                # PHY reconfigured for the combo
    assert b"respf -CHAN=5 -PCODE=9" in tx   # answers polls on that combo
    assert dev.mode == "RESPF"


def test_start_does_not_invent_a_phantom_from_the_config_ok():
    # only the uwbcfg query is scripted (no answered poll). The config-set "ok"
    # must be FLUSHED before respf, so poll_events can't miscount it as an
    # answered poll -> the report stays empty.
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = TransponderController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({})

    assert ctrl.status({})["answered"] == []


def test_start_records_an_answered_poll_into_status():
    # board answers the uwbcfg query, and an initiator polls us on respf
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY, "respf": RESPF_REPLY})
    ctrl = TransponderController(dev, now=_fixed_clock(1000.0), dwell=0)
    ctrl.start({})

    answered = ctrl.status({})["answered"]
    assert len(answered) == 1   # exactly the real initiator, no phantom "ok"
    a = answered[0]
    assert a["addr"] == "0x00AB"
    assert a["channel"] == 5
    assert a["pcode"] == 9
    assert a["poll_count"] == 1
    assert a["first_seen"] == 1000.0
    assert a["distance_cm"] == 77


def test_stop_sends_stop_and_leaves_the_device_idle():
    dev, ser = make_device()
    ctrl = TransponderController(dev, now=_fixed_clock())
    ctrl.stop({})
    assert b"stop\r\n" in bytes(ser.tx)
    assert dev.mode == "STOP"
    assert ctrl.status({})["running"] is False


def test_status_reports_progress_and_a_jsonable_snapshot():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = TransponderController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({})

    st = ctrl.status({})
    assert st["total"] == 8            # default 2 channels x 4 pcodes
    assert st["step"] >= 1             # the first combo has been driven
    assert st["running"] is True
    assert isinstance(st["answered"], list)
    json.dumps(st)                     # the whole status must be JSON-able


def test_blank_args_use_defaults_and_custom_args_are_honored():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = TransponderController(dev, now=_fixed_clock(), dwell=0)

    ctrl.start({})  # blank -> channels 5/9, pcodes 9-12
    assert ctrl.status({})["total"] == 8

    # ";" is the wire-form list separator (control.py reserves "," for pairs)
    ctrl.start({"channels": "5;9", "pcodes": "9;10"})
    assert ctrl.status({})["total"] == 4


def test_step_drives_the_next_combo_without_threads():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = TransponderController(dev, now=_fixed_clock(), dwell=0)
    # two combos: (5, 9) then (9, 9)
    ctrl.start({"channels": "5,9", "pcodes": "9"})  # drives the first, (5, 9)

    assert ctrl.step() is True   # drives the second, (9, 9)
    tx = bytes(ser.tx)
    assert b"uwbcfg 9 " in tx
    assert b"respf -CHAN=9 -PCODE=9" in tx

    assert ctrl.step() is False  # cycle exhausted


# ---- repeated-poll dwell (bug uwb-qorvo-4hg), ported from the scanner ------
# The transponder previously did a single drain with no dwell at all. Under
# the shared SweepController base (bead 1hu.20) it now dwells the same way
# the (hardware-validated) scanner does: the budget is spent as several short
# sub-poll slices, draining after each and stopping early on a fresh hit. This
# is a behavior IMPROVEMENT, not a regression — a late-arriving initiator poll
# that used to be missed on the very first combo is now caught.

def test_run_step_dwells_after_respf_so_late_replies_are_captured():
    # On hardware an initiator's poll can arrive AFTER respf has started, so a
    # cycle that drains immediately can see nothing. The step MUST dwell
    # before polling. Model that: the poll is delivered ONLY when the
    # injected sleep (the dwell) fires — with no dwell, no answered record.
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})  # NOTE: no respf reply
    def dwell_delivers(_secs):
        ser.feed(RESPF_REPLY)  # the initiator polls during the dwell window
    ctrl = TransponderController(dev, now=_fixed_clock(1000.0),
                                  sleep=dwell_delivers, dwell=0.5)
    ctrl.start({"channels": "9", "pcodes": "9"})

    answered = ctrl.status({})["answered"]
    assert len(answered) == 1
    assert answered[0]["addr"] == "0x00AB"
    assert answered[0]["channel"] == 9


def test_run_step_sleeps_for_the_configured_dwell():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    slept: list[float] = []
    ctrl = TransponderController(dev, now=_fixed_clock(),
                                  sleep=lambda s: slept.append(s), dwell=0.75)
    ctrl.start({"channels": "9", "pcodes": "9"})
    # the dwell budget is spent across several short sub-poll slices (bug
    # 4hg, ported from the scanner), so assert the TOTAL slept equals the
    # configured dwell, not that a single 0.75s sleep happened. No poll
    # lands, so every slice is used.
    assert sum(slept) == pytest.approx(0.75)

    assert ctrl.step() is False  # cycle exhausted


def test_first_combo_late_reply_is_captured_by_repeated_polling():
    # Mirrors bug uwb-qorvo-4hg for the transponder: the FIRST combo (the one
    # run synchronously inside start()) must repeatedly drain poll_events()
    # across the dwell budget, not just once. Model an initiator that only
    # polls after a few sub-poll slices: the poll is fed on the 3rd slice, so
    # a lone drain sees nothing while repeated draining captures it.
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})  # NOTE: no respf reply
    calls = {"n": 0}

    def late_reply(_secs):
        calls["n"] += 1
        if calls["n"] == 3:            # answers only after a couple sub-polls
            ser.feed(RESPF_REPLY)

    ctrl = TransponderController(dev, now=_fixed_clock(1000.0),
                                  sleep=late_reply, dwell=0.5)
    ctrl.start({"channels": "9", "pcodes": "9"})  # single combo == the first

    answered = ctrl.status({})["answered"]
    assert len(answered) == 1
    assert answered[0]["addr"] == "0x00AB"
    assert answered[0]["channel"] == 9


# ---- running-clears-on-exhaustion (bug uwb-qorvo-09r), ported from the -----
# ---- scanner ----------------------------------------------------------------

def test_running_clears_when_the_cycle_is_exhausted():
    # Previously status()['running'] stayed True forever after the cycle
    # exhausted (only stop() cleared it). Under the shared base (bead 1hu.20)
    # this is fixed for the transponder too: once every combo has been
    # driven (index >= total) the cycle is DONE and running must report False
    # WITHOUT an explicit stop — so the port-arbiter pump handoff resumes the
    # passive listener on natural completion.
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = TransponderController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({"channels": "5,9", "pcodes": "9"})   # two combos, first driven
    assert ctrl.status({})["running"] is True        # combo 2 still pending

    assert ctrl.step() is True    # drive combo 2 (now index == total)
    assert ctrl.step() is False   # exhausted
    assert ctrl.status({})["running"] is False
