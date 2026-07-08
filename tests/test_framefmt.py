"""Host-compiled tests for firmware/ble/framefmt.c.

frame_encode() renders one received UWB frame (bytes + diagnostics) as the
compact JSON pushed out the frame characteristic (6e5f0003-...). Pure logic,
no SDK includes — compiles with host gcc like detector.c.

Harness protocol (stdin -> stdout), one frame per line:
  F <hexbytes|-> <ts5hex> <cfo_pphm> <rsl100> <fsl100> <seq>
prints the encoded JSON line.
"""

import json
import os
import subprocess

import pytest

HERE = os.path.dirname(__file__)
FRAMEFMT_C = os.path.join(HERE, "..", "firmware", "ble", "framefmt.c")
HARNESS_C = os.path.join(HERE, "c", "framefmt_harness.c")

FRAME_HEX_MAX = 16  # bytes of frame payload included in "b"


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    exe = tmp_path_factory.mktemp("cframe") / "framefmt_harness"
    subprocess.run(
        ["gcc", "-Wall", "-Werror", "-O1",
         "-I", os.path.dirname(FRAMEFMT_C),
         FRAMEFMT_C, HARNESS_C, "-o", exe],
        check=True)
    return exe


def run1(exe, data: bytes, ts: bytes, cfo_pphm: int, rsl100: int,
         fsl100: int, seq: int, crc: int = 1) -> str:
    line = " ".join([
        "F", data.hex().upper() if data else "-", ts.hex().upper(),
        str(cfo_pphm), str(rsl100), str(fsl100), str(seq), str(crc)])
    out = subprocess.run([exe], input=line + "\n",
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def fmt100(v: int) -> str:
    sign = "-" if v < 0 else ""
    return f"{sign}{abs(v) // 100}.{abs(v) % 100:02d}"


def oracle(data: bytes, ts: bytes, cfo_pphm: int, rsl100: int,
           fsl100: int, seq: int, crc: int = 1) -> str:
    b = data[:FRAME_HEX_MAX].hex().upper()
    if len(data) > FRAME_HEX_MAX:
        b += "+"
    ts32 = f"0x{ts[4]:02X}{ts[3]:02X}{ts[2]:02X}{ts[1]:02X}"
    return (f'{{"i":{seq},"n":{len(data)},"b":"{b}",'
            f'"rsl":{fmt100(rsl100)},"fsl":{fmt100(fsl100)},'
            f'"o":{fmt100(cfo_pphm)},"ts":"{ts32}","crc":{crc}}}')


CASES = [
    # typical blink-ish frame, negative levels, negative cfo
    (bytes.fromhex("41880CADDE"), bytes([0x10, 0x32, 0x54, 0x76, 0x98]),
     -325, -7950, -8120, 7),
    # empty frame (rxDataLen 0 happens on some error paths)
    (b"", bytes(5), 0, 0, 0, 0),
    # long frame -> truncated hex with '+'
    (bytes(range(40)), bytes([1, 2, 3, 4, 5]), 149, -6001, -6099, 12345),
    # exactly FRAME_HEX_MAX bytes -> no '+'
    (bytes(range(FRAME_HEX_MAX)), bytes([0xFF] * 5), 100, -100, -199, 1),
    # sub-1.0 negative values keep their sign ("-0.50")
    (b"\xAA", bytes([0, 0, 0, 0, 0x80]), -50, -50, -99, 2),
]


class TestFrameEncode:
    @pytest.mark.parametrize("data,ts,cfo,rsl,fsl,seq", CASES)
    def test_matches_oracle(self, harness, data, ts, cfo, rsl, fsl, seq):
        assert run1(harness, data, ts, cfo, rsl, fsl, seq) == \
            oracle(data, ts, cfo, rsl, fsl, seq)

    @pytest.mark.parametrize("data,ts,cfo,rsl,fsl,seq", CASES)
    def test_is_valid_json(self, harness, data, ts, cfo, rsl, fsl, seq):
        doc = json.loads(run1(harness, data, ts, cfo, rsl, fsl, seq))
        assert doc["n"] == len(data)
        assert doc["i"] == seq
        assert doc["crc"] == 1

    def test_crc_failed_flag(self, harness):
        out = run1(harness, bytes.fromhex("492B0100"), bytes(5),
                   0, -8000, -8100, 5, crc=0)
        assert json.loads(out)["crc"] == 0
        assert out == oracle(bytes.fromhex("492B0100"), bytes(5),
                             0, -8000, -8100, 5, crc=0)

    def test_fits_one_notification(self, harness):
        # worst case must fit PAYLOAD_MAX (128) alongside MTU 131
        out = run1(harness, bytes([0xFF] * 127), bytes([0xFF] * 5),
                   -99999, -9999, -9999, 4294967295)
        assert len(out) <= 128


FRAG_CHUNK = 40  # bytes of frame carried per fragment notification


def run_frag(exe, data: bytes, seq: int, part: int) -> str:
    line = f"G {data.hex().upper() if data else '-'} {seq} {part}"
    out = subprocess.run([exe], input=line + "\n",
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def frag_count(n: int) -> int:
    return (n + FRAG_CHUNK - 1) // FRAG_CHUNK if n > 0 else 0


class TestFragmentEncode:
    # frames of assorted lengths, incl. the 69-byte AirTag reference size,
    # exact multiples of the chunk, and the 127-byte max PSDU
    FRAMES = [
        bytes(range(69)),
        bytes(range(40)),      # exactly one chunk
        bytes(range(80)),      # exactly two chunks
        bytes([0xAB] * 127),   # max 802.15.4 PSDU
        bytes.fromhex("492B0100" + "FF" * 65),  # airtag-ish header + body
    ]

    @pytest.mark.parametrize("frame", FRAMES)
    def test_fragments_reassemble_to_original(self, harness, frame):
        q = frag_count(len(frame))
        assert q >= 1
        parts = {}
        for p in range(q):
            doc = json.loads(run_frag(harness, frame, seq=1234, part=p))
            assert doc["i"] == 1234
            assert doc["p"] == p
            assert doc["q"] == q
            parts[p] = doc["b"]
        # every part present exactly once, in-order concat rebuilds the frame
        assert set(parts) == set(range(q))
        rebuilt = "".join(parts[p] for p in range(q))
        assert rebuilt == frame.hex().upper()

    @pytest.mark.parametrize("frame", FRAMES)
    def test_each_fragment_fits_one_notification(self, harness, frame):
        q = frag_count(len(frame))
        for p in range(q):
            assert len(run_frag(harness, frame, seq=4294967295, part=p)) <= 128

    def test_out_of_range_part_emits_nothing(self, harness):
        assert run_frag(harness, bytes(range(69)), seq=1, part=99) == ""

    def test_empty_frame_has_no_fragments(self, harness):
        assert frag_count(0) == 0
        assert run_frag(harness, b"", seq=1, part=0) == ""


def run_enc(exe, seq, phe, crcb, stse, to):
    line = f"S {seq} {phe} {crcb} {stse} {to}"
    out = subprocess.run([exe], input=line + "\n",
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def enc_oracle(seq, phe, crcb, stse, to):
    return (f'{{"i":{seq},"enc":1,"phe":{phe},"crcb":{crcb},'
            f'"stse":{stse},"to":{to}}}')


class TestEncryptedEncode:
    ENC_CASES = [
        (1, 0, 3, 3, 0),          # typical AirTag STS burst: CRC-bad + STS err
        (0, 0, 0, 0, 0),          # nothing yet
        (65535, 4095, 4095, 255, 4095),  # wide values
    ]

    @pytest.mark.parametrize("seq,phe,crcb,stse,to", ENC_CASES)
    def test_matches_oracle(self, harness, seq, phe, crcb, stse, to):
        assert run_enc(harness, seq, phe, crcb, stse, to) == \
            enc_oracle(seq, phe, crcb, stse, to)

    @pytest.mark.parametrize("seq,phe,crcb,stse,to", ENC_CASES)
    def test_valid_json(self, harness, seq, phe, crcb, stse, to):
        doc = json.loads(run_enc(harness, seq, phe, crcb, stse, to))
        assert doc["enc"] == 1
        assert doc["i"] == seq

    def test_fits_one_notification(self, harness):
        out = run_enc(harness, 4294967295, 4095, 4095, 255, 4095)
        assert len(out) <= 128
