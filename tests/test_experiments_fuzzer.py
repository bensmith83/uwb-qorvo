"""TDD (RED phase) for the FuzzerController — a deliberately-triggered UWB
malformed-frame fuzzer (uwb-qorvo-1hu.16).

*** AUTHORIZED SECURITY-RESEARCH TOOLING ONLY. Fire fuzz cases only at devices
*** you own / are explicitly authorized to test. Never point this at
*** infrastructure or third-party devices.

FuzzerController does NOT itself transmit a malformed frame — that firmware
support (``fuzztx <case_id>``) is bead uwb-qorvo-1hu.15, deferred to hardware.
This controller only:

  1. emits the ``fuzztx <case_id>`` command matching the shared fuzz-case
     catalog (``CASES``/``CASE_IDS``/``CASE_NAMES``), ordered by id:
     0=bad-crc, 1=invalid-frametype, 2=oversized-phr, 3=truncated-mac,
     4=illegal-sts;
  2. switches the board to LISTENER mode to capture any nearby-device
     reaction to the malformed frame;
  3. drains ``device.poll_events()`` right after the switch and logs whatever
     showed up into a structured, timestamped "reactions" list.

As with the other controllers, all hardware and real time are kept OUT of the
tested seam: the clock (``now``) is injected so timestamps are deterministic,
and the "board's reaction" is scripted via ``tests.test_device.make_scripted``
(the reply lands in the fake serial's rx buffer the moment the matching
command is written, so no thread/sleep is needed to observe it).

The module under test does not exist yet; the failing import IS the RED
signal. GREEN implements ``uwb_explorer/experiments/fuzzer.py``.
"""

from __future__ import annotations

import json

from tests.test_device import make_device, make_scripted

from uwb_explorer.experiments.fuzzer import (
    CASE_IDS,
    CASE_NAMES,
    CASES,
    DEFAULT_CASE,
    FuzzerController,
)

# a LISTENER-mode reaction frame (LSTN pseudo-JSON, per docs/cli-protocol.md /
# tests/test_parser.py::test_listener_frame_pseudo_json_hex_array)
REACTION_FRAME = (
    b'JS00EF{"LSTN":[49,2B,01,00,26,13,00,FF,18,5A],'
    b'"TS":"0xCE99FA8D","O":253}\r\n'
)


def _fixed_clock(t=1000.0):
    return lambda: t


# ---- 1. the fuzz-case catalog ----------------------------------------------

def test_catalog_is_ordered_by_id_with_the_pinned_cases():
    names = [c["name"] for c in CASES]
    assert names == [
        "bad-crc", "invalid-frametype", "oversized-phr",
        "truncated-mac", "illegal-sts",
    ]
    ids = [c["id"] for c in CASES]
    assert ids == [0, 1, 2, 3, 4]


def test_case_ids_and_names_are_inverse_maps():
    for c in CASES:
        assert CASE_IDS[c["name"]] == c["id"]
        assert CASE_NAMES[c["id"]] == c["name"]


def test_default_case_is_bad_crc():
    assert DEFAULT_CASE == "bad-crc"
    assert CASE_IDS[DEFAULT_CASE] == 0


# ---- 2. firing a case sends fuzztx <id> ------------------------------------

def test_start_defaults_to_bad_crc_and_sends_fuzztx_0():
    dev, ser = make_device()
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.start({})
    assert b"fuzztx 0\r\n" in bytes(ser.tx)
    assert ctrl.status({})["last_case"] == "bad-crc"


def test_start_honors_the_named_case_arg():
    dev, ser = make_device()
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.start({"case": "oversized-phr"})
    assert b"fuzztx 2\r\n" in bytes(ser.tx)
    assert ctrl.status({})["last_case"] == "oversized-phr"


def test_start_with_every_catalog_case_sends_the_matching_id():
    for case in CASES:
        dev, ser = make_device()
        ctrl = FuzzerController(dev, now=_fixed_clock())
        ctrl.start({"case": case["name"]})
        assert f"fuzztx {case['id']}\r\n".encode() in bytes(ser.tx)


def test_start_rejects_an_unknown_case_name():
    dev, _ = make_device()
    ctrl = FuzzerController(dev, now=_fixed_clock())
    try:
        ctrl.start({"case": "not-a-real-case"})
        raise AssertionError("expected ValueError for an unknown fuzz case")
    except ValueError:
        pass


# ---- 3. after firing, the board switches to LISTENER and captures reactions

def test_start_switches_to_listener_after_firing():
    dev, ser = make_device()
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.start({})
    tx = bytes(ser.tx)
    assert b"fuzztx 0\r\n" in tx
    assert b"listener\r\n" in tx
    # fuzztx must be sent BEFORE the switch to listener
    assert tx.index(b"fuzztx") < tx.index(b"listener")
    assert dev.mode == "LISTENER"


def test_start_captures_a_scripted_post_fire_reaction_with_a_timestamp():
    dev, ser = make_scripted({"fuzztx": b"ok\r\n", "listener": REACTION_FRAME})
    ctrl = FuzzerController(dev, now=_fixed_clock(1234.5))
    ctrl.start({})

    reactions = ctrl.status({})["reactions"]
    assert len(reactions) == 1
    r = reactions[0]
    assert r["timestamp"] == 1234.5
    assert r["type"] == "frame"
    # payload survives as a JSON-able hex string, not raw bytes
    assert r["payload"] == "492b01002613 00ff185a".replace(" ", "")


def test_start_does_not_leak_the_fuzztx_ack_into_reactions():
    # fuzztx's own "ok" ack must be flushed before the listener switch so it
    # isn't misread as a device reaction (mirrors the beacon/scanner's
    # documented flush-before-fire convention against phantom events).
    dev, ser = make_scripted({"fuzztx": b"ok\r\n", "listener": REACTION_FRAME})
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.start({})

    reactions = ctrl.status({})["reactions"]
    assert len(reactions) == 1
    assert reactions[0]["type"] == "frame"


def test_start_with_no_reaction_leaves_an_empty_list():
    dev, ser = make_scripted({"fuzztx": b"ok\r\n"})  # no listener reply scripted
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.start({})
    assert ctrl.status({})["reactions"] == []


def test_restarting_replaces_the_reactions_log():
    dev, ser = make_scripted({"fuzztx": b"ok\r\n", "listener": REACTION_FRAME})
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.start({"case": "bad-crc"})
    assert len(ctrl.status({})["reactions"]) == 1

    ctrl.start({"case": "illegal-sts"})  # fresh fire -> fresh log, not appended
    assert len(ctrl.status({})["reactions"]) == 1
    assert ctrl.status({})["last_case"] == "illegal-sts"


# ---- 4. status / stop -------------------------------------------------------

def test_status_reports_running_true_after_start():
    dev, _ = make_device()
    ctrl = FuzzerController(dev, now=_fixed_clock())
    assert ctrl.status({})["running"] is False   # not started yet

    ctrl.start({})
    st = ctrl.status({})
    assert st["running"] is True
    assert st["last_case"] == "bad-crc"
    assert st["reactions"] == []
    json.dumps(st)   # the whole status must be JSON-able


def test_stop_sends_stop_and_clears_running_restoring_idle():
    dev, ser = make_device()
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.start({})
    assert ctrl.status({})["running"] is True

    ctrl.stop({})
    assert b"stop\r\n" in bytes(ser.tx)
    assert dev.mode == "STOP"
    assert ctrl.status({})["running"] is False


def test_stop_without_a_prior_start_does_not_crash():
    dev, ser = make_device()
    ctrl = FuzzerController(dev, now=_fixed_clock())
    ctrl.stop({})
    assert b"stop\r\n" in bytes(ser.tx)
    assert ctrl.status({})["running"] is False
