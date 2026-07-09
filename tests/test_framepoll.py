"""Host-compiled tests for firmware/ble/framepoll.c.

frame_poll_select() is the emit-priority decision split out of
uwb_feed.c's frame_poll so it is host-testable. The behavior that
matters: a staged captured frame (have_cap) always wins, so Apple's rare
interleaved CRC-good data frames — drained from the OK ISR into the same
buffer the F1 CRC-fail path uses — are never lost under the SP3 ranging
flood. Pure logic, no SDK — compiles with host gcc.

Harness protocol (stdin -> stdout), one decision per line:
  S <have_cap> <fresh> <dlen> <sts_on> <have_rng> <enc_changed>
prints the chosen emit: NONE|CAP|RANGING|CLEAN|RNG|ENC
"""

import os
import subprocess

import pytest

HERE = os.path.dirname(__file__)
FRAMEPOLL_C = os.path.join(HERE, "..", "firmware", "ble", "framepoll.c")
HARNESS_C = os.path.join(HERE, "c", "framepoll_harness.c")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    exe = tmp_path_factory.mktemp("cfp") / "framepoll_harness"
    subprocess.run(
        ["gcc", "-Wall", "-Werror", "-O1",
         "-I", os.path.dirname(FRAMEPOLL_C),
         FRAMEPOLL_C, HARNESS_C, "-o", exe],
        check=True)
    return exe


def sel(exe, have_cap, fresh, dlen, sts_on, have_rng, enc):
    line = f"S {have_cap} {fresh} {dlen} {sts_on} {have_rng} {enc}"
    out = subprocess.run([exe], input=line + "\n",
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_capture_wins_over_everything(harness):
    # a staged capture beats a fresh SP3 flood, ranging, rng, enc
    assert sel(harness, 1, 1, 0, 1, 1, 1) == "CAP"
    assert sel(harness, 1, 0, 0, 0, 0, 0) == "CAP"
    # even when the newest ring frame is itself a data frame
    assert sel(harness, 1, 1, 69, 0, 0, 0) == "CAP"


def test_ranging_when_sts_and_no_data(harness):
    assert sel(harness, 0, 1, 0, 1, 0, 0) == "RANGING"


def test_clean_when_fresh(harness):
    # fresh data-bearing frame
    assert sel(harness, 0, 1, 69, 0, 0, 0) == "CLEAN"
    # fresh + dlen==0 + STS off keeps prior behavior (CLEAN, not RANGING)
    assert sel(harness, 0, 1, 0, 0, 0, 0) == "CLEAN"
    # fresh + dlen>0 + sts_on is NOT ranging (ranging needs dlen==0)
    assert sel(harness, 0, 1, 69, 1, 0, 0) == "CLEAN"


def test_fallback_priority_rng_then_enc(harness):
    assert sel(harness, 0, 0, 0, 0, 1, 1) == "RNG"
    assert sel(harness, 0, 0, 0, 0, 0, 1) == "ENC"


def test_none_when_nothing_new(harness):
    assert sel(harness, 0, 0, 0, 0, 0, 0) == "NONE"
