"""TDD for the web dashboard's pure state model (the 'Geiger counter').

The model is fed raw LSTAT counter dicts (monotonic PHY event counters from
the board) and turns them into a phone-friendly, JSON-able view: per-poll
deltas, a rolling activity level, history for a sparkline, and peaks. It holds
no serial or HTTP concerns so it can be tested without hardware.
"""

from __future__ import annotations

from uwb_explorer.webmodel import DetectorState


def test_first_update_sets_baseline_no_phantom_activity():
    s = DetectorState()
    # First reading only establishes a baseline; monotonic counters carry
    # history from before we started, so the first delta must be zero.
    snap = s.update({"SFDD": 100, "PHE": 5, "CRCB": 0, "CRCG": 0})
    assert snap["hits"] == 0
    assert snap["level"] == "idle"
    assert snap["total"] == 0


def test_delta_counts_new_frame_events():
    s = DetectorState()
    s.update({"SFDD": 100, "PHE": 0, "CRCB": 0, "CRCG": 0})
    snap = s.update({"SFDD": 106, "PHE": 2, "CRCB": 0, "CRCG": 0})
    # 6 new SFD detections + 2 new PHY-header errors = 8 "hits"
    assert snap["hits"] == 8
    assert snap["delta"]["SFDD"] == 6
    assert snap["delta"]["PHE"] == 2
    assert snap["total"] == 8
    assert snap["level"] != "idle"


def test_counter_reset_is_treated_as_new_baseline():
    # The listener gets restarted (e.g. channel sweep) and counters drop.
    s = DetectorState()
    s.update({"SFDD": 500, "PHE": 0, "CRCB": 0, "CRCG": 0})
    snap = s.update({"SFDD": 3, "PHE": 0, "CRCB": 0, "CRCG": 0})
    # Must NOT report -497 hits; a decrease means the counter wrapped/reset.
    assert snap["hits"] == 0


def test_activity_level_escalates_with_rate():
    s = DetectorState()
    s.update({"SFDD": 0})
    low = s.update({"SFDD": 1})["level"]
    s2 = DetectorState()
    s2.update({"SFDD": 0})
    high = s2.update({"SFDD": 900})["level"]
    order = ["idle", "low", "medium", "high"]
    assert order.index(high) > order.index(low)


def test_good_crc_is_flagged_as_decoded():
    s = DetectorState()
    s.update({"CRCG": 0})
    snap = s.update({"CRCG": 3})
    assert snap["decoded"] == 3
    assert snap["hits"] == 3


def test_history_grows_and_is_bounded():
    s = DetectorState(history=4)
    for i in range(10):
        s.update({"SFDD": i})
    snap = s.snapshot()
    assert len(snap["history"]) == 4
    # history holds recent per-poll hit counts (ints), newest last
    assert all(isinstance(x, int) for x in snap["history"])


def test_snapshot_carries_channel_and_pcode_when_set():
    s = DetectorState()
    s.set_config(channel=9, pcode=11)
    snap = s.snapshot()
    assert snap["channel"] == 9
    assert snap["pcode"] == 11


def test_peak_tracks_max_hits_per_poll():
    s = DetectorState()
    s.update({"SFDD": 0})
    s.update({"SFDD": 10})   # +10
    s.update({"SFDD": 13})   # +3
    assert s.snapshot()["peak"] == 10
