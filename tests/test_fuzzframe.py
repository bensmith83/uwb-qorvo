"""Host-compiled tests for firmware/ble/fuzzframe.c.

The UWB Fuzzer's malformed-802.15.4z-frame builders (bead uwb-qorvo-1hu.15).
Each builder deliberately produces a frame malformed in exactly one way; these
tests assert the malformation is present and independently checkable from the
builder output (not merely trusting a flag). Pure logic, no SDK includes —
compiles with host gcc like framefmt.c / detector.c.

Harness protocol (stdin -> stdout), one command per line:
  Z <case_id>  -> "<hex|-> <len> <phr> <has_phr> <sts_sp> <sts_len> <illegal>"
  X <argstr>   -> "<rc> <order|-> <hex|-> <len>"  (CLI + half-duplex path)

ETHICS/SCOPE: authorized security-research tooling; deliberately malformed
frames; opcode-triggered emission only. See docs/EXPERIMENTS.md.
"""

import os
import subprocess

import pytest

HERE = os.path.dirname(__file__)
FUZZ_C = os.path.join(HERE, "..", "firmware", "ble", "fuzzframe.c")
HARNESS_C = os.path.join(HERE, "c", "fuzzframe_harness.c")

# case ids (shared catalog — must match fuzzframe.h and the Python controller)
BAD_CRC, INVALID_FRAMETYPE, OVERSIZED_PHR, TRUNCATED_MAC, ILLEGAL_STS = range(5)
PSDU_MAX = 127


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    exe = tmp_path_factory.mktemp("cfuzz") / "fuzzframe_harness"
    subprocess.run(
        ["gcc", "-Wall", "-Werror", "-O1",
         "-I", os.path.dirname(FUZZ_C),
         FUZZ_C, HARNESS_C, "-o", exe],
        check=True)
    return exe


def fcs(data: bytes) -> int:
    """802.15.4 FCS — CRC-16/KERMIT (poly 0x1021 reflected, init 0)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF


class Built:
    def __init__(self, line: str):
        parts = line.split()
        hexs = parts[0]
        self.data = b"" if hexs == "-" else bytes.fromhex(hexs)
        (self.len, self.phr, self.has_phr, self.sts_sp, self.sts_len,
         self.illegal) = (int(parts[1]), int(parts[2]), int(parts[3]),
                          int(parts[4]), int(parts[5]), int(parts[6]))

    @property
    def fcf(self) -> int:
        return self.data[0] | (self.data[1] << 8)

    @property
    def frame_type(self) -> int:
        return self.fcf & 0x07

    @property
    def dest_addr_mode(self) -> int:
        return (self.fcf >> 10) & 0x03

    @property
    def src_addr_mode(self) -> int:
        return (self.fcf >> 14) & 0x03


def build(exe, case_id: int) -> Built:
    out = subprocess.run([exe], input=f"Z {case_id}\n",
                         capture_output=True, text=True, check=True)
    line = out.stdout.strip()
    assert line and line != "ERR", f"builder rejected case {case_id}"
    return Built(line)


def cli(exe, arg: str):
    """Run fuzz_cli(arg); returns (rc, order, tx_bytes)."""
    token = "-" if arg == "" else arg
    out = subprocess.run([exe], input=f"X {token}\n",
                         capture_output=True, text=True, check=True)
    rc, order, hexs, _ln = out.stdout.strip().split()
    data = b"" if hexs == "-" else bytes.fromhex(hexs)
    return int(rc), ("" if order == "-" else order), data


class TestBadCrc:
    def test_fcs_mismatches_computed_crc(self, harness):
        # a valid frame with a corrupted 2-byte FCS: recomputing the FCS over
        # the body must NOT equal the embedded (little-endian) FCS.
        b = build(harness, BAD_CRC)
        body, embedded = b.data[:-2], b.data[-2] | (b.data[-1] << 8)
        assert embedded != fcs(body)

    def test_frame_otherwise_wellformed(self, harness):
        # only the FCS is wrong: the header still parses as a data frame
        b = build(harness, BAD_CRC)
        assert b.frame_type == 1  # data frame
        assert b.has_phr == 0 and b.sts_sp == -1


class TestInvalidFrametype:
    def test_frametype_is_reserved(self, harness):
        b = build(harness, INVALID_FRAMETYPE)
        assert b.frame_type == 0x07  # 7 = Reserved in 802.15.4z

    def test_fcs_is_valid_so_only_type_is_wrong(self, harness):
        # the malformation is isolated to the FCF: the FCS still matches, so a
        # receiver rejects the frame purely on the illegal frame type.
        b = build(harness, INVALID_FRAMETYPE)
        body, embedded = b.data[:-2], b.data[-2] | (b.data[-1] << 8)
        assert embedded == fcs(body)


class TestOversizedPhr:
    def test_phr_exceeds_real_payload_and_legal_max(self, harness):
        b = build(harness, OVERSIZED_PHR)
        assert b.has_phr == 1
        actual_psdu = b.len - 1  # bytes following the PHR byte
        assert b.phr > actual_psdu       # claims more than is present
        assert b.phr > PSDU_MAX          # and more than the legal maximum

    def test_leading_byte_is_the_phr(self, harness):
        b = build(harness, OVERSIZED_PHR)
        assert b.data[0] == b.phr


class TestTruncatedMac:
    def test_addressing_declared_but_absent(self, harness):
        b = build(harness, TRUNCATED_MAC)
        # FCF promises short dest+src addressing...
        assert b.dest_addr_mode != 0 and b.src_addr_mode != 0
        # ...but the frame is cut short before those fields exist. Minimum for
        # FCF(2)+seq(1)+destPAN(2)+dest(2)+src(2)+FCS(2) = 11 octets.
        assert b.len < 11


class TestIllegalSts:
    def test_sts_present_but_zero_length(self, harness):
        b = build(harness, ILLEGAL_STS)
        assert b.illegal == 1
        # independently checkable inconsistency: an STS packet config that
        # says STS is present (SP >= 1) while the STS length is zero.
        assert b.sts_sp >= 1
        assert b.sts_len == 0


class TestBuildDispatch:
    @pytest.mark.parametrize("case_id", range(5))
    def test_every_case_builds(self, harness, case_id):
        b = build(harness, case_id)
        assert b.len <= 160

    def test_unknown_case_is_rejected(self, harness):
        out = subprocess.run([harness], input="Z 9\n",
                             capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "ERR"


class TestFuzzTxCli:
    @pytest.mark.parametrize("case_id", range(5))
    def test_cli_builds_and_emits_case(self, harness, case_id):
        rc, order, tx = cli(harness, str(case_id))
        assert rc == case_id
        # half-duplex: listener paused, exactly one TX, listener resumed
        assert order == "PTR"
        # the emitted bytes are exactly the builder's output for that case
        assert tx == build(harness, case_id).data

    def test_cli_leading_space_ok(self, harness):
        rc, order, tx = cli(harness, "  2")
        assert rc == OVERSIZED_PHR and order == "PTR"

    @pytest.mark.parametrize("arg", ["", "9", "x", "-1", "5"])
    def test_cli_bad_arg_transmits_nothing(self, harness, arg):
        rc, order, tx = cli(harness, arg)
        assert rc == -1
        assert order == ""   # listener never paused, radio never keyed
        assert tx == b""
