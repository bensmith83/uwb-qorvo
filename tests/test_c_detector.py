"""Host-compiled tests for firmware/ble/detector.c.

The C port of uwb_explorer.webmodel.DetectorState + blecodec.encode_state
must match the Python implementation BYTE FOR BYTE — Python is the oracle.
detector.c is pure logic (no SDK includes), so it compiles with host gcc.

Harness protocol (stdin -> stdout):
  U <sfdd> <phe> <crcb> <crcg>   feed one counter poll (no output)
  E <status> <chan> <pcode>      encode; chan/pcode may be 'null'; prints JSON
"""

import json
import os
import subprocess

import pytest

from uwb_explorer.blecodec import encode_state
from uwb_explorer.webmodel import DetectorState

HERE = os.path.dirname(__file__)
DETECTOR_C = os.path.join(HERE, "..", "firmware", "ble", "detector.c")
HARNESS_C = os.path.join(HERE, "c", "detector_harness.c")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    exe = tmp_path_factory.mktemp("cdet") / "detector_harness"
    subprocess.run(
        ["gcc", "-Wall", "-Werror", "-O1",
         "-I", os.path.dirname(DETECTOR_C),
         DETECTOR_C, HARNESS_C, "-o", exe],
        check=True)
    return exe


def run_harness(exe, lines):
    out = subprocess.run([exe], input="\n".join(lines) + "\n",
                         capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def oracle(polls, status="live", channel=9, pcode=9):
    st = DetectorState()
    for p in polls:
        st.update({"SFDD": p[0], "PHE": p[1], "CRCB": p[2], "CRCG": p[3]})
    snap = st.snapshot()
    snap["status"] = status
    snap["channel"] = channel
    snap["pcode"] = pcode
    return encode_state(snap).decode()


def enc_cmd(status="live", channel=9, pcode=9):
    c = "null" if channel is None else str(channel)
    k = "null" if pcode is None else str(pcode)
    return f"E {status} {c} {k}"


class TestCDetectorMatchesPython:
    def test_first_poll_is_baseline_only(self, harness):
        polls = [(1000, 50, 3, 7)]
        got = run_harness(harness, ["U 1000 50 3 7", enc_cmd()])
        assert got == [oracle(polls)]
        assert json.loads(got[0])["h"] == 0  # no phantom pre-start backlog

    def test_hits_sum_deltas_and_decoded(self, harness):
        polls = [(0, 0, 0, 0), (6, 2, 0, 0), (6, 2, 0, 5)]
        cmds = ["U 0 0 0 0", "U 6 2 0 0", "U 6 2 0 5", enc_cmd()]
        got = run_harness(harness, cmds)
        assert got == [oracle(polls)]
        obj = json.loads(got[0])
        assert obj["d"] == 5 and obj["t"] == 13

    def test_negative_delta_clamped(self, harness):
        # 12-bit chip counters wrap; a decrease must not produce hits
        polls = [(0, 4000, 0, 0), (0, 5, 0, 0)]
        got = run_harness(harness, ["U 0 4000 0 0", "U 0 5 0 0", enc_cmd()])
        assert got == [oracle(polls)]
        assert json.loads(got[0])["h"] == 0

    @pytest.mark.parametrize("hits,level", [
        (0, "idle"), (1, "low"), (9, "low"),
        (10, "medium"), (99, "medium"), (100, "high"), (5000, "high")])
    def test_level_thresholds(self, harness, hits, level):
        polls = [(0, 0, 0, 0), (hits, 0, 0, 0)]
        got = run_harness(harness,
                          [f"U 0 0 0 0", f"U {hits} 0 0 0", enc_cmd()])
        assert got == [oracle(polls)]
        assert json.loads(got[0])["l"] == level

    def test_peak_tracks_max_single_poll(self, harness):
        polls = [(0, 0, 0, 0), (10, 0, 0, 0), (13, 0, 0, 0)]
        got = run_harness(
            harness, ["U 0 0 0 0", "U 10 0 0 0", "U 13 0 0 0", enc_cmd()])
        assert got == [oracle(polls)]
        assert json.loads(got[0])["p"] == 10

    def test_null_channel_and_pcode(self, harness):
        got = run_harness(
            harness,
            ["U 0 0 0 0", enc_cmd(status="waiting", channel=None,
                                  pcode=None)])
        assert got == [oracle([(0, 0, 0, 0)], status="waiting",
                              channel=None, pcode=None)]
        assert '"c":null,"k":null' in got[0]

    def test_payload_under_mtu(self, harness):
        got = run_harness(
            harness,
            ["U 0 0 0 0", "U 4000000 4000000 4000000 4000000", enc_cmd()])
        assert len(got[0]) < 128
